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
from typing import Any

import aiosqlite
import httpx

from bleep.config import Settings
from bleep.graph.deps import Approvals
from bleep.guard.log import GuardLogger
from bleep.incidents.bus import EventBus
from bleep.incidents.repository import IncidentRepo, utcnow
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
    Decision,
    Diagnosis,
    Proposal,
    Report,
    VerificationResult,
)
from bleep.schemas.incidents import TERMINAL_STATUSES, Incident
from bleep.schemas.settings import SecretStatus
from bleep.schemas.telegram import TelegramStatus
from bleep.store.db import Database
from bleep.store.secrets import SecretStore, Source

log = logging.getLogger(__name__)

TOKEN_ENV = "TELEGRAM_BOT_TOKEN"  # `Settings.telegram_bot_token`
CHAT_ID_ENV = "TELEGRAM_CHAT_ID"  # `Settings.telegram_chat_id`


class Notifier:
    """Fans each call out to the bus and to Telegram. `telegram` is None without a token."""

    def __init__(
        self,
        bus: EventBus,
        telegram: TelegramClient | None = None,
        chat_id: str | None = None,
        messages: MessageLog | None = None,
        approvals: Approvals | None = None,
    ) -> None:
        self.bus = bus
        self.telegram = telegram
        self.chat_id = chat_id
        self.messages = messages
        self.approvals = approvals
        self.expectations: Expecting | None = None
        self._clearing: set[asyncio.Task[None]] = set()
        self.incidents: IncidentRepo | None = None

    async def incident_finished(self, incident_id: str) -> None:
        """Remove the buttons from every message logged for an incident that ended: a message
        that still shows Approve, Reject or "It's expected" means the question is open."""
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

    async def drain(self) -> None:
        """Wait for the clearing in flight (tests, and the app's shutdown)."""
        await asyncio.gather(*self._clearing, return_exceptions=True)

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
            except (httpx.HTTPError, OSError, RuntimeError) as error:  # not mapped by `call`
                log.warning("telegram: could not clear the buttons (%s)", type(error).__name__)

    async def _clear_if_finished(self, incident_id: str, message_id: int, approval_id: str) -> None:
        """A message logged after its incident ended or its plan was closed (the run's Approval
        node can still send one just after an outside close) never met the clear: do it now."""
        if self.approvals is not None and approval_id:
            current = await self.approvals.current(incident_id)
            finished = current is not None and current.id == approval_id and current.closed
        elif self.incidents is not None:
            incident = await self.incidents.get(incident_id)
            finished = incident is not None and incident.status in TERMINAL_STATUSES
        else:
            finished = False
        if finished:
            await self._clear([message_id])

    async def incident_opened(self, incident: Incident) -> None:
        """A host-unreachable incident that waits for its host says so instead of promising a
        plan. When "This is expected" applies, the message carries its button and is logged, so
        the press finds the incident."""
        name = "incident_waiting" if incident.status == "waiting" else "incident_opened"
        buttons = await self._expect_button(incident.id)
        if not buttons:
            await self._plain(name, id=incident.id, title=incident.title)
            return
        assert self.telegram is not None and self.chat_id is not None
        text = fmt.render(name, id=incident.id, title=incident.title)
        try:
            message_id = await self.telegram.send_message(self.chat_id, text, buttons)
        except TelegramError as error:
            log.warning("telegram: could not send: %s", error)
            return
        if self.messages is not None:
            await self.messages.add(message_id, incident.id, "", utcnow())
            await self._clear_if_finished(incident.id, message_id, "")

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
                id=incident.id,
                title=fmt.prose_line(incident.title),
                root_cause=fmt.prose_line(cause),
                reasons=_reasons(proposal),
                steps=_refused_steps(proposal),
            )
            await self._ask(incident, approval, text, approvable=False, reject_label="Let it go")
            return
        text = fmt.render(
            "proposal",
            id=incident.id,
            title=fmt.prose_line(incident.title),
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
        if current.proposal.refused:
            text = fmt.render(
                "refused_reminder", id=incident.id, title=fmt.prose_line(incident.title)
            )
            await self._safely(
                self._ask(incident, approval, text, approvable=False, reject_label="Let it go")
            )
            return
        text = fmt.render(
            "reminder",
            id=incident.id,
            title=fmt.prose_line(incident.title),
            steps=fmt.steps_text(current.proposal),
        )
        approvable = fmt.shows_in_full(current.proposal)
        await self._safely(self._ask(incident, approval, text, approvable=approvable))

    async def verification(self, incident: Incident, result: VerificationResult) -> None:
        name = "verification_ok" if result.ok else "verification_failed"
        await self._plain(name, id=incident.id, detail=result.detail)

    async def report(self, incident: Incident, report: Report) -> None:
        await self._plain(
            "report",
            id=incident.id,
            title=report.title,
            outcome=report.outcome,
            summary=report.summary,
            cause=report.cause,
            action=report.action_taken or "nothing ran",
            decision=report.human_decision or "you were not asked",
            follow_ups="\n".join(f"- {item}" for item in report.follow_ups) or "nothing",
        )

    async def notice(self, incident_id: str, text: str) -> None:
        await self._plain("notice", id=incident_id, text=text)

    async def _ask(
        self,
        incident: Incident,
        approval: ApprovalRequest,
        text: str,
        *,
        approvable: bool = True,
        reject_label: str = "Reject",
    ) -> None:
        """Send a plan with its buttons and remember which incident the message is about. A plan
        the message cannot show in full (`fmt.shows_in_full`) gets Reject only."""
        assert self.telegram is not None and self.chat_id is not None
        buttons = [("Approve", f"approve:{approval.id}")] if approvable else []
        buttons.append((reject_label, f"reject:{approval.id}"))
        buttons += await self._expect_button(incident.id)
        message_id = await self.telegram.send_message(self.chat_id, text, buttons)
        if self.messages is not None:
            await self.messages.add(message_id, incident.id, approval.id, utcnow())
            await self._clear_if_finished(incident.id, message_id, approval.id)

    async def _plain(self, template: str, **values: str) -> None:
        if self.telegram is None:
            return
        assert self.chat_id is not None
        await self._safely(self.telegram.send_message(self.chat_id, fmt.render(template, **values)))

    @staticmethod
    async def _safely(sending) -> None:
        try:
            await sending
        except TelegramError as error:
            log.warning("telegram: could not send: %s", error)


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

    def rebuild() -> NotifySetup:
        return setup(settings, db, bus, approvals, guard_log, secrets=secrets)

    if token is None or chat_id is None:
        if token is not None:
            log.warning("telegram: a bot token is set but TELEGRAM_CHAT_ID is not; Telegram is off")
        return NotifySetup(
            Notifier(bus),
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
    notifier = Notifier(bus, bot.client, chat_id, messages, approvals)
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
