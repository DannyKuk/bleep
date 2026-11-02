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
from dataclasses import dataclass, field

import httpx

from bleep.config import Settings
from bleep.graph.deps import Approvals
from bleep.guard.log import GuardLogger
from bleep.incidents.bus import EventBus
from bleep.incidents.repository import utcnow
from bleep.notify import format as fmt
from bleep.notify.telegram import (
    MessageLog,
    Resumer,
    TelegramBot,
    TelegramClient,
    TelegramError,
)
from bleep.schemas.graph import ApprovalRequest, Diagnosis, Proposal, Report, VerificationResult
from bleep.schemas.incidents import Incident
from bleep.store.db import Database

log = logging.getLogger(__name__)


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

    async def incident_opened(self, incident: Incident) -> None:
        await self._plain("incident_opened", id=incident.id, title=incident.title)

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
        text = fmt.render(
            "proposal",
            id=incident.id,
            title=incident.title,
            category=category,
            confidence=confidence,
            root_cause=cause,
            steps=fmt.steps_text(proposal),
            reason=proposal.reason,
        )
        await self._ask(incident, approval, text)

    async def reminder(self, incident: Incident, approval: ApprovalRequest) -> None:
        await self.bus.publish(incident.id, "reminder", {"approval_id": approval.id})
        if self.telegram is None:
            return
        current = await self.approvals.current(incident.id) if self.approvals else None
        if current is None or current.id != approval.id:
            return  # no plan to show
        text = fmt.render(
            "reminder", id=incident.id, title=incident.title, steps=fmt.steps_text(current.proposal)
        )
        await self._safely(self._ask(incident, approval, text))

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

    async def _ask(self, incident: Incident, approval: ApprovalRequest, text: str) -> None:
        """Send a plan with its buttons and remember which incident the message is about."""
        assert self.telegram is not None and self.chat_id is not None
        buttons = [("Approve", f"approve:{approval.id}"), ("Reject", f"reject:{approval.id}")]
        message_id = await self.telegram.send_message(self.chat_id, text, buttons)
        if self.messages is not None:
            await self.messages.add(message_id, incident.id, approval.id, utcnow())

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


@dataclass
class NotifySetup:
    """The notifier for `GraphDeps`, and the bot's start and stop for the app's lifespan."""

    notifier: Notifier
    bot: TelegramBot | None = None
    _http: httpx.AsyncClient | None = None
    _task: asyncio.Task[None] | None = field(default=None, repr=False)

    def start(self, runner: Resumer) -> None:
        """Start polling. Does nothing without a token."""
        if self.bot is not None and self._task is None:
            self.bot.runner = runner
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


def setup(
    settings: Settings,
    db: Database,
    bus: EventBus,
    approvals: Approvals,
    guard_log: GuardLogger | None = None,
) -> NotifySetup:
    """Build the notifier, and the bot too when a token and a chat id are configured.

    The runner does not exist yet when the graph's dependencies are built, so `start(runner)`
    gives it to the bot later.
    """
    token = settings.telegram_bot_token
    if token is None or settings.telegram_chat_id is None:
        if token is not None:
            log.warning("telegram: a bot token is set but TELEGRAM_CHAT_ID is not; Telegram is off")
        return NotifySetup(Notifier(bus))
    # httpx logs every request URL at INFO, and the Bot API puts the token in the URL.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    http = httpx.AsyncClient()
    messages = MessageLog(db)
    chat_id = settings.telegram_chat_id
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
    return NotifySetup(notifier, bot, http)


class _NoRunner:
    """Stands in until `start(runner)`; a decision cannot arrive before the bot is polling."""

    async def resume(self, incident_id, decision, approval_id) -> None:
        raise RuntimeError("the Telegram bot was used before it was started")
