"""The notifier the graph and the reminder loop call: the dashboard bus plus, if configured,
Telegram.

The nodes publish what the dashboard shows (`proposal`, `approval_requested`, `notice`) to the bus
themselves, so the notifier adds to the bus only what no node publishes (`reminder`) and carries
every call to Telegram. With no bot token there is no Telegram and every call is a no-op beyond
that. A Telegram outage never breaks a run: only `proposal` lets the error out, because the
Approval node tells the dashboard when a plan could not be sent.
"""

import asyncio
import logging
import sqlite3
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import aiosqlite
import httpx

from bleep.config import Settings
from bleep.graph.deps import Approvals
from bleep.graph.nodes.report import HEALTHY_ANYWAY, OUTCOME_TEXT
from bleep.guard.log import GuardLogger
from bleep.guard.titles import split_service_id, title_name
from bleep.incidents.bus import EventBus
from bleep.incidents.repository import IncidentRepo, primary_signal, utcnow
from bleep.notify import format as fmt
from bleep.notify.telegram import (
    EXPECTED_LABEL,
    EXPECTED_VERB,
    Expecting,
    MessageLog,
    Resumer,
    TelegramBot,
    TelegramClient,
    TelegramError,
    mask_chat_id,
)
from bleep.schemas.graph import (
    ApprovalRequest,
    Check,
    Decision,
    Diagnosis,
    Proposal,
    Report,
    VerificationResult,
)
from bleep.schemas.incidents import TERMINAL_STATUSES, Ending, Incident
from bleep.schemas.settings import SecretStatus
from bleep.schemas.telegram import TelegramStatus
from bleep.store.db import Database
from bleep.store.secrets import SecretStore, Source

log = logging.getLogger(__name__)

TOKEN_ENV = "TELEGRAM_BOT_TOKEN"  # `Settings.telegram_bot_token`
CHAT_ID_ENV = "TELEGRAM_CHAT_ID"  # `Settings.telegram_chat_id`
DRAIN_TIMEOUT_S = 5.0  # how long a shutdown waits for buttons still being cleared
# A new incident on a subject whose last one ended this recently is "Again": the longest repeat
# hold (#356). A folded repeat is "Again" whenever (#380).
AGAIN_WITHIN = timedelta(hours=24)
MAX_NEXT = 3  # follow-ups in the end message; the dashboard keeps them all
_HOST_KINDS = ("disk", "memory", "load", "host_unreachable")
_END_OF = {
    "resolved": "end_fixed",
    "unverified": "end_unverified",
    "rejected": "end_rejected",
    "no_action": "end_no_action",
    "budget": "end_stopped",
    "replan_limit": "end_stopped",
}
_CHECKED = ("end_fixed", "end_healthy", "end_unverified")  # ends that come after a check


class Notifier:
    """Fans each call out to the bus and to Telegram. `telegram` is None without a token.

    Every incident is one thread in the chat: its first message (the opener) says it is new or
    again, every later one replies to it, and the last one leads with the outcome (#380).
    `incidents` gives the short names, the signals and the previous incident; `wiring.py` sets it
    once the repository exists."""

    def __init__(
        self,
        bus: EventBus,
        telegram: TelegramClient | None = None,
        chat_id: str | None = None,
        messages: MessageLog | None = None,
        approvals: Approvals | None = None,
        *,
        incidents: IncidentRepo | None = None,
        dashboard_url: str | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.bus = bus
        self.telegram = telegram
        self.chat_id = chat_id
        self.messages = messages
        self.approvals = approvals
        self.expectations: Expecting | None = None
        self._clearing: set[asyncio.Task[None]] = set()
        self.incidents = incidents
        self.dashboard_url = dashboard_url
        self.clock = clock
        # One lock per incident with a message on its way, so the check for its opener and the
        # log of the message that becomes it are one step (`_send`).
        self._threads: dict[str, asyncio.Lock] = {}

    async def incident_finished(self, incident_id: str) -> None:
        """Remove the buttons from every message logged for an incident that ended: a message
        that still shows Approve, Reject or "It's expected" means the question is open. Its lock
        goes too, unless a message holds it: its end message may still be on its way."""
        lock = self._threads.get(incident_id)
        if lock is not None and not lock.locked():
            del self._threads[incident_id]
        if self.messages is not None:
            self._spawn(self.messages.ids_for_incident(incident_id))

    async def approval_decided(
        self, incident_id: str, approval_id: str | None, decision: Decision
    ) -> None:
        """Remove the buttons from every message of the plan a decision answered (the proposal
        and its reminders), whichever channel decided and however it was said: a press clears
        only its own message, and a reply clears none."""
        if self.messages is not None and approval_id:
            self._spawn(self.messages.ids_for_approval(approval_id))

    async def drain(self, timeout_s: float = DRAIN_TIMEOUT_S) -> None:
        """Wait for the clearing in flight (tests, and the app's shutdown) for at most
        `timeout_s`, so a hanging Telegram cannot stall a shutdown."""
        if self._clearing:
            await asyncio.wait(set(self._clearing), timeout=timeout_s)

    def _spawn(self, ids: Coroutine[Any, Any, list[int]]) -> None:
        """Clear in the background, so a slow Telegram never holds a close or a decision."""
        task = asyncio.create_task(self._look_up_and_clear(ids))
        self._clearing.add(task)
        task.add_done_callback(self._clearing.discard)

    async def _look_up_and_clear(self, ids: Coroutine[Any, Any, list[int]]) -> None:
        try:
            message_ids = await ids
        except sqlite3.Error, aiosqlite.Error, OSError:
            log.exception("telegram: could not look up the messages to clear")
            return
        await self._clear(message_ids)

    async def _clear(self, message_ids: list[int]) -> None:
        """Clear each message on its own: one that fails never skips the rest."""
        if self.telegram is None or self.chat_id is None:
            return
        for message_id in message_ids:
            try:
                await self.telegram.clear_buttons(self.chat_id, message_id)
            except TelegramError as error:
                if "message is not modified" in str(error):  # a press cleared it already
                    log.debug("telegram: buttons already gone from message %s", message_id)
                else:
                    log.warning("telegram: could not clear the buttons: %s", error)
            except Exception:  # a bug must keep its traceback, and the other messages still clear
                log.exception("telegram: clearing message %s failed unexpectedly", message_id)

    async def _clear_if_finished(self, incident_id: str, message_id: int, approval_id: str) -> None:
        """A message logged after its incident ended or its plan was closed (the run's Approval
        node can still send one just after an outside close) never met the clear: do it now."""
        try:
            finished = await self._finished(incident_id, approval_id)
        except Exception:  # best effort: the message is sent, and the Approval node must know it
            log.exception("incident %s: could not tell whether to clear a new message", incident_id)
            return
        if finished:
            await self._clear([message_id])

    async def _finished(self, incident_id: str, approval_id: str) -> bool:
        if self.approvals is not None and approval_id:
            current = await self.approvals.current(incident_id)
            if current is None or current.id != approval_id:
                return False
            return current.closed or current.decision is not None
        if self.incidents is not None:
            incident = await self.incidents.get(incident_id)
            return incident is not None and incident.status in TERMINAL_STATUSES
        return False

    async def incident_opened(self, incident: Incident) -> None:
        """The incident's opener: what was seen, and that Bleep looks into it, or, for a
        host-unreachable incident that waits for its host, that it waits. When "This is expected"
        applies, the message carries its button."""
        if self.telegram is None:
            return
        name = "incident_waiting" if incident.status == "waiting" else "incident_opened"
        body = f"{await self._seen(incident)} {fmt.render(name)}".strip()
        buttons = await self._expect_button(incident.id)
        await self._safely(self._send(incident, body, buttons=buttons, says_what=True))

    async def _expect_button(self, incident_id: str) -> list[tuple[str, str]]:
        """The "It's expected" button when it applies to the incident, else none."""
        if self.telegram is None or self.expectations is None:
            return []
        try:
            offered = await self.expectations.offered(incident_id)
        except Exception:  # the message goes out without the button
            log.exception("incident %s: could not tell whether it can be expected", incident_id)
            return []
        return [(EXPECTED_LABEL, EXPECTED_VERB)] if offered is not None else []

    async def proposal(
        self,
        incident: Incident,
        diagnosis: Diagnosis | None,
        proposal: Proposal,
        approval: ApprovalRequest,
    ) -> None:
        if self.telegram is None:
            return
        cause, category, confidence = fmt.confidence_text(diagnosis)
        if proposal.refused:
            text = fmt.render(
                "refused",
                root_cause=fmt.prose_line(cause),
                reasons=_reasons(proposal),
                steps=_refused_steps(proposal),
            )
            await self._ask(incident, approval, text, approvable=False, reject_label="Let it go")
            return
        text = fmt.render(
            "proposal",
            category=category,
            confidence=confidence,
            root_cause=fmt.prose_line(cause),
            steps=fmt.steps_text(proposal),
            reason=fmt.prose_line(proposal.reason),
        )
        await self._ask(incident, approval, text, approvable=fmt.shows_in_full(proposal))

    async def reminder(self, incident: Incident, approval: ApprovalRequest) -> None:
        await self.bus.publish(incident.id, "reminder", {"approval_id": approval.id})
        if self.telegram is None:
            return
        current = await self.approvals.current(incident.id) if self.approvals else None
        if current is None or current.id != approval.id:
            return  # no plan to show
        waited = fmt.duration(self.clock() - current.requested_at)
        if current.proposal.refused:
            text = fmt.render("refused_reminder", waited=waited)
            await self._safely(
                self._ask(incident, approval, text, approvable=False, reject_label="Let it go")
            )
            return
        text = fmt.render("reminder", waited=waited, steps=fmt.steps_text(current.proposal))
        approvable = fmt.shows_in_full(current.proposal)
        await self._safely(self._ask(incident, approval, text, approvable=approvable))

    async def verification(self, incident: Incident, result: VerificationResult) -> None:
        """A check that failed says so; one that passed is told in the end message."""
        if self.telegram is None or result.ok:
            return
        text = fmt.bold_first_line(fmt.render("verification_failed", detail=result.detail))
        await self._safely(self._send(incident, text))

    async def report(self, incident: Incident, report: Report) -> None:
        """The end message: the outcome first, the cause, what ran, what was checked, what is
        left for the person and a link to the incident's page."""
        if self.telegram is not None:
            await self._safely(self._report(incident, report))

    async def _report(self, incident: Incident, report: Report) -> None:
        name = _end_template(report.outcome)
        if name == "end_no_action" and await self._let_go(incident.id):
            name = "end_rejected"
        took = fmt.duration(self.clock() - incident.opened_at)
        values = {"end_fixed": {"took": took}, "end_healthy": {"took": took}}.get(name, {})
        if name == "end_stopped":
            values = {"outcome": report.outcome}
        parts = [
            fmt.bold_first_line(fmt.render(name, **values)),
            fmt.render(
                "end_report",
                cause=fmt.prose_line(report.cause),
                action=fmt.prose_line(report.action_taken or fmt.phrase("ran_nothing")),
            ),
        ]
        if name in _CHECKED and (checks := await self._checked(incident.id)):
            parts.append(fmt.render("end_checked", checks=checks))
        if report.follow_ups:
            items = report.follow_ups[:MAX_NEXT]
            listed = fmt.Redacted("\n".join(f"- {fmt.prose_line(item)}" for item in items))
            parts.append(fmt.render("end_next", follow_ups=listed))
        if self.dashboard_url:
            url = f"{self.dashboard_url.rstrip('/')}/incidents/{incident.id}"
            parts.append(fmt.render("end_details", url=fmt.Redacted(url)))
        # Should the incident have ended already (a close that raced the report), the outcome
        # goes out as a note.
        note = fmt.message(fmt.render("notice", text=report.outcome), *parts[1:])
        await self._send(incident, fmt.message(*parts), end=True, instead=note)

    async def _let_go(self, incident_id: str) -> bool:
        """Whether the person let a plan Bleep's own checks refused go: a no-action run that ends
        as theirs, as a rejected plan does."""
        current = await self.approvals.current(incident_id) if self.approvals else None
        return (
            current is not None
            and current.proposal.refused
            and current.decision is not None
            and current.decision.kind == "reject"
        )

    async def _checked(self, incident_id: str) -> fmt.Redacted | None:
        """The checks of the run's last verification, from its timeline, as one line."""
        events = [e for e in await self.bus.history(incident_id) if e.kind == "verification"]
        if not events:
            return None
        checks = [Check.model_validate(c) for c in events[-1].payload.get("checks", [])]
        return fmt.prose_line("; ".join(f"{c.target} {c.detail}".strip() for c in checks)) or None

    async def notice(self, incident_id: str, text: str, ending: Ending | None = None) -> None:
        """A note in the middle of a run, or, with `ending`, the end message of an incident that
        closed outside its run's report; one that comes after the end message is a note. A
        folded repeat's return (`back`) arrives silently: its wait was told, the end is
        only so the thread does not stop at "waits"."""
        if self.telegram is not None:
            await self._safely(self._notice(incident_id, fmt.prose_line(text), ending))

    async def _notice(self, incident_id: str, text: str, ending: Ending | None) -> None:
        assert self.telegram is not None and self.chat_id is not None
        incident = await self.incidents.get(incident_id) if self.incidents is not None else None
        note = fmt.render("notice", text=text)
        if incident is None:  # no thread to put it in
            await self.telegram.send_message(self.chat_id, note)
            return
        if ending is None:
            await self._send(incident, note)
            return
        if ending == "back":
            took = fmt.duration(self.clock() - incident.opened_at)
            rendered = fmt.bold_first_line(fmt.render("end_back", took=took, text=text))
        else:
            rendered = fmt.bold_first_line(fmt.render(f"end_{ending}", text=text))
        silent = ending == "back" and incident.fold_of is not None
        await self._send(incident, rendered, end=True, instead=note, silent=silent)

    async def _ask(
        self,
        incident: Incident,
        approval: ApprovalRequest,
        text: str,
        *,
        approvable: bool = True,
        reject_label: str = "Reject",
    ) -> None:
        """Send a plan with its buttons and remember which incident and plan the message is
        about. A plan the message cannot show in full (`fmt.shows_in_full`) gets Reject only."""
        buttons = [("Approve", f"approve:{approval.id}")] if approvable else []
        buttons.append((reject_label, f"reject:{approval.id}"))
        buttons += await self._expect_button(incident.id)
        await self._send(
            incident, fmt.bold_first_line(text), buttons=buttons, approval_id=approval.id
        )

    async def _send(
        self,
        incident: Incident,
        text: str,
        *,
        buttons: list[tuple[str, str]] | None = None,
        approval_id: str = "",
        says_what: bool = False,
        end: bool = False,
        instead: str = "",
        silent: bool = False,
    ) -> None:
        """Send a message of the incident's thread and log it. The first one that reaches
        Telegram is the opener: it leads with the incident's head and what was seen (unless
        `says_what`: its text already does), and is not a reply, or replies to the previous
        incident's last message for a repeat. Every later one is a reply to the opener. An `end`
        message is sent as `instead` when the incident's end message went out already. One
        message of an incident at a time, so two can never both become its opener."""
        assert self.telegram is not None and self.chat_id is not None
        async with self._threads.setdefault(incident.id, asyncio.Lock()):
            if end and self.messages is not None and await self.messages.ended(incident.id):
                text, end = instead, False
            reply_to = await self.messages.first_for(incident.id) if self.messages else None
            if reply_to is None:
                head, reply_to = await self._opening(incident)
                text = fmt.message(head, "" if says_what else await self._seen(incident), text)
            message_id = await self.telegram.send_message(
                self.chat_id, text, buttons, reply_to=reply_to, silent=silent
            )
            if self.messages is None:
                return
            await self.messages.add(
                message_id, incident.id, approval_id, self.clock(), bool(buttons), end
            )
        if buttons:
            await self._clear_if_finished(incident.id, message_id, approval_id)

    async def _opening(self, incident: Incident) -> tuple[str, int | None]:
        """The opener's head line, and the message it replies to: none for a new incident, the
        previous incident's last message for a repeat (`Again`)."""
        name = incident.short_name or incident.id
        before = await self.incidents.previous(incident) if self.incidents is not None else None
        if before is not None and before.closed_at is not None:
            gap = incident.opened_at - before.closed_at
            if incident.fold_of is not None or gap < AGAIN_WITHIN:
                head = fmt.render(
                    "head_again",
                    incident=name,
                    after=fmt.duration(gap),
                    earlier=before.short_name or before.id,
                )
                quoted = await self.messages.last_for(before.id) if self.messages else None
                return fmt.bold_first_line(head), quoted
        host = incident.subject.kind == "host"
        where = fmt.phrase("where_host") if host else incident.subject.host_id
        return fmt.bold_first_line(fmt.render("head_new", incident=name, where=where)), None

    async def _seen(self, incident: Incident) -> str:
        """One line of what opened the incident, from its most telling signal."""
        signals = await self.incidents.signals(incident.id) if self.incidents is not None else []
        signal = primary_signal(signals)
        if signal is None:
            return ""
        if signal.kind in _HOST_KINDS:
            return fmt.render(f"seen/{signal.kind}", host=incident.subject.host_id)
        service = split_service_id(incident.subject)[1]
        if signal.kind == "url_failed":
            url = str(signal.detail.get("url") or service)
            return fmt.render("seen/url_failed", url=title_name(url))
        container = signal.container or service
        return fmt.render(f"seen/{signal.kind}", container=title_name(container))

    @staticmethod
    async def _safely(sending) -> None:
        """Await a send; Telegram failing, or the store its threads are kept in, is logged and
        never reaches the run."""
        try:
            await sending
        except TelegramError as error:
            log.warning("telegram: could not send: %s", error)
        except (sqlite3.Error, aiosqlite.Error, OSError) as error:
            log.warning("telegram: could not keep the thread: %s", type(error).__name__)


def _end_template(outcome: str) -> str:
    """The end message's template for the report's fixed outcome text."""
    if outcome == HEALTHY_ANYWAY:
        return "end_healthy"
    for kind, text in OUTCOME_TEXT.items():
        if outcome.startswith(text):
            return _END_OF[kind]
    return "end_stopped"


def _refused_steps(proposal: Proposal) -> fmt.Redacted:
    """The steps the plan check refused, as `steps_text` shows a plan's steps."""
    shown = fmt.steps_text(proposal.model_copy(update={"steps": proposal.refused_steps}))
    return shown if shown.strip() else fmt.Redacted("no steps")


def _reasons(proposal: Proposal) -> fmt.Redacted:
    return fmt.Redacted("\n".join(f"- {fmt.prose_line(text)}" for text in proposal.refusal_reasons))


@dataclass
class NotifySetup:
    """The notifier for `GraphDeps`, and the bot's start and stop for the app's lifespan."""

    notifier: Notifier
    bot: TelegramBot | None = None
    _http: httpx.AsyncClient | None = None
    _task: asyncio.Task[None] | None = field(default=None, repr=False)
    chat_id: str | None = None
    token_set: bool = False
    token_source: Source | None = None
    _rebuild: Callable[[], NotifySetup] | None = field(default=None, repr=False)
    _runner: Resumer | None = field(default=None, repr=False)
    _expectations: Expecting | None = field(default=None, repr=False)

    def status(self) -> TelegramStatus:
        """What the settings page shows: set up or not, the chat masked, when it last polled."""
        return TelegramStatus(
            configured=self.bot is not None,
            chat_id=mask_chat_id(self.chat_id) if self.chat_id is not None else None,
            last_poll=self.bot.last_poll if self.bot is not None else None,
            token=SecretStatus(env=TOKEN_ENV, set=self.token_set, source=self.token_source),
        )

    async def reload(self) -> None:
        """Build the bot again from the token and the chat id in force now, and poll with it: a
        change on the dashboard takes effect without a restart. The notifier object stays, since
        the graph, the runner and the reminders hold it; a message being sent while the old
        client closes fails as a `TelegramError`, as in an outage. The same bot goes on from the
        last update it confirmed, so a change of the chat id alone gets nothing delivered twice;
        an update being handled when the poller stops is cut off, as on a restart."""
        if self._rebuild is None:
            return
        old_bot = self.bot
        await self.stop()
        await self.close()
        fresh = self._rebuild()
        if fresh.bot is not None and old_bot is not None:
            fresh.bot.continue_from(old_bot)
        old, new = self.notifier, fresh.notifier
        old.telegram, old.chat_id, old.messages = new.telegram, new.chat_id, new.messages
        old.approvals = new.approvals
        self.bot, self._http, self.chat_id = fresh.bot, fresh._http, fresh.chat_id
        self.token_set, self.token_source = fresh.token_set, fresh.token_source
        if self._runner is not None:
            self.start(self._runner)

    def use_expectations(self, expectations: Expecting) -> None:
        """ "This is expected" for the buttons; it needs the runner, so it comes after setup."""
        self._expectations = expectations
        self.notifier.expectations = expectations

    def start(self, runner: Resumer) -> None:
        """Start polling. Does nothing without a token."""
        self._runner = runner
        if self.bot is not None and self._task is None:
            self.bot.runner = runner
            self.bot.expectations = self._expectations
            self._task = asyncio.create_task(self.bot.poll_forever(), name="telegram poller")

    async def stop(self) -> None:
        """Stop polling. The client stays open: runs and reminders still send until they stop."""
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def close(self) -> None:
        """Close the Telegram client, after everything that sends has stopped."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None


def setup(
    settings: Settings,
    db: Database,
    bus: EventBus,
    approvals: Approvals,
    guard_log: GuardLogger | None = None,
    *,
    secrets: SecretStore | None = None,
) -> NotifySetup:
    """Build the notifier, and the bot too when a token and a chat id are configured: from
    `secrets` when given (the env, else the dashboard), else from `settings`.

    The runner does not exist yet when the graph's dependencies are built, so `start(runner)`
    gives it to the bot later; `reload()` builds the bot again after a change on the dashboard.
    """
    token, chat_id, source = settings.telegram_bot_token, settings.telegram_chat_id, None
    if secrets is not None:
        token, source = secrets.get(TOKEN_ENV), secrets.state(TOKEN_ENV).source
        chat = secrets.get(CHAT_ID_ENV)
        chat_id = chat.get_secret_value() if chat is not None else None

    dashboard_url = str(settings.dashboard_url) if settings.dashboard_url else None

    def rebuild() -> NotifySetup:
        return setup(settings, db, bus, approvals, guard_log, secrets=secrets)

    if token is None or chat_id is None:
        if token is not None:
            log.warning("telegram: a bot token is set but TELEGRAM_CHAT_ID is not; Telegram is off")
        return NotifySetup(
            Notifier(bus, dashboard_url=dashboard_url),
            chat_id=chat_id,
            token_set=token is not None,
            token_source=source,
            _rebuild=rebuild,
        )
    # httpx logs every request URL at INFO, and the Bot API puts the token in the URL.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    http = httpx.AsyncClient()
    messages = MessageLog(db)
    bot = TelegramBot(
        token.get_secret_value(),
        chat_id,
        approvals,
        _NoRunner(),
        http,
        messages=messages,
        guard_log=guard_log,
    )
    notifier = Notifier(bus, bot.client, chat_id, messages, approvals, dashboard_url=dashboard_url)
    return NotifySetup(
        notifier,
        bot,
        http,
        chat_id=chat_id,
        token_set=True,
        token_source=source,
        _rebuild=rebuild,
    )


class _NoRunner:
    """Stands in until `start(runner)`; a decision cannot arrive before the bot is polling."""

    async def resume(self, incident_id, decision, approval_id) -> None:
        raise RuntimeError("the Telegram bot was used before it was started")
