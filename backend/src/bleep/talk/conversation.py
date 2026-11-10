"""Answer what the person writes to Bleep, from Bleep's own state, with one cheap-model call.

Both surfaces call `Talk.reply`: the Telegram bot now, the dashboard's panel later (#391). It
reads; it never changes anything. The person's text, the conversation, the incident it is about
and the snapshot all reach the model as untrusted data (`guard_text`), and the model only picks
a kind and, for a question, writes the answer. An action comes back as an offer, checked by code
against Bleep's state (`talk.offers`), that the surface confirms with a button; a refusal, an
unclear message, a failure and an offer that fails a check are answered with fixed lines from
`prompts/talk/`, never with the model's words (docs/design/notify.md).
"""

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, tzinfo

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

from bleep import prompts
from bleep.guard.log import GuardLogger
from bleep.guard.pipeline import guard_text
from bleep.guard.redact import redact_text
from bleep.guard.sanitize import strip_control
from bleep.incidents.repository import utcnow
from bleep.llm.factory import Role
from bleep.llm.guard import ModelGuard
from bleep.llm.structured import invoke_structured
from bleep.llm.usage import (
    CHAT_USAGE,
    BudgetExceeded,
    CostLedger,
    UsageTracker,
    check_chat_cap,
)
from bleep.schemas.incidents import TERMINAL_STATUSES
from bleep.schemas.settings import Budgets
from bleep.schemas.talk import (
    IncidentFacts,
    Offer,
    ReplyKind,
    Snapshot,
    TalkReading,
    TalkReply,
    Turn,
)
from bleep.talk.looks import look_offer
from bleep.talk.offers import check, confirm_line, instruction
from bleep.talk.state import StateReader

log = logging.getLogger(__name__)

TALK_USAGE = CHAT_USAGE  # the usage ledger's key for chat answers, with a daily cap of its own
TEXT_CHARS = 1000  # the person's message, as the model reads it
TURN_CHARS = 600  # each side of an earlier turn
STATE_CHARS = 16_000  # the snapshot; its tail is kept if a large setup outgrows it
INCIDENT_CHARS = 4000
HISTORY_CHARS = 8000  # the turns a surface keeps (`talk.history.TURNS_KEPT`), each side capped
ANSWER_CHARS = 1500  # an answer Bleep sends, well under Telegram's 4,096
# A chat answer must not hold the person for long: one call of at most this long, one retry,
# and the whole reading (a JSON fallback and its retry included) cut off after REPLY_TIMEOUT_S.
TIMEOUT_S = 30
RETRIES = 1
REPLY_TIMEOUT_S = 60
# A link in an answer: a scheme, `www.`, or a host name followed by a path. Telegram makes them
# clickable, and a name or a cause from a host could carry one.
_LINK = re.compile(
    r"\b(?:[a-z][a-z0-9+.-]*://|www\.)\S+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)+/\S*", re.IGNORECASE
)
ModelFactory = Callable[[Role], BaseChatModel]


def _json(value: BaseModel | list[Turn] | None) -> str:
    if isinstance(value, list):
        return json.dumps([t.model_dump() for t in value], ensure_ascii=False, indent=1)
    if value is None:
        return "null"
    # Compact, and without the fields that only say their default: the snapshot of a large setup
    # stays well inside STATE_CHARS.
    return value.model_dump_json(exclude_none=True, exclude_defaults=True)


def _fixed(name: str, **values: str) -> str:
    return prompts.load(f"talk/{name}", **values).strip()


def clean_answer(text: str) -> str:
    """The model's answer as a chat may show it: no control characters, secrets redacted, no
    links, capped."""
    clean = redact_text(strip_control(text)).strip()
    clean = _LINK.sub(_fixed("link_removed"), clean)
    return clean if len(clean) <= ANSWER_CHARS else clean[: ANSWER_CHARS - 1].rstrip() + "…"


def _no_zone() -> tzinfo | None:
    return None


class Talk:
    """`models` builds the chat model for a role (the `cheap` one is used); `budgets` gives the
    chat's own daily cost cap and the model timeout, `ledger` records what each call cost.
    `zone` gives Bleep's time zone at each message (None: UTC), in which a snooze's time is read
    and said."""

    def __init__(
        self,
        state: StateReader,
        models: ModelFactory,
        *,
        budgets: Callable[[], Budgets] = Budgets,
        ledger: CostLedger | None = None,
        guard_log: GuardLogger | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        zone: Callable[[], tzinfo | None] = _no_zone,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.state = state
        self.models = models
        self.budgets = budgets
        self.ledger = ledger
        self.guard_log = guard_log
        self.sleep = sleep
        self.zone = zone
        self.clock = clock

    async def reply(
        self, text: str, incident_id: str | None = None, turns: Sequence[Turn] = ()
    ) -> TalkReply:
        """Read `text`, said about the incident `incident_id` if any after the earlier `turns`
        of this conversation, and say what Bleep answers. Never raises for a model that fails:
        that is the `failed` reply, which still offers to send the words to a plan that waits
        on the incident, as a reply to it could before."""
        facts: IncidentFacts | None = None
        try:
            async with asyncio.timeout(REPLY_TIMEOUT_S):
                snapshot = await self.state.snapshot()
                facts = await self.state.incident(incident_id) if incident_id else None
                reading = await self._read(text, snapshot, facts, list(turns))
                spent = _asks_look(reading) and await self._runs_spent()
        except BudgetExceeded:
            return self._fallback("failed", _fixed("spent"), text, facts, incident_id)
        except Exception as error:  # the type only: a provider's message may quote its key
            log.warning("talk: could not read a message (%s)", type(error).__name__)
            return self._fallback("failed", _fixed("failed"), text, facts, incident_id)
        try:
            return self._answer(reading, text, snapshot, facts, incident_id, spent)
        except Exception:  # a bug checking a reading must still answer, and keep its traceback
            log.exception("talk: could not answer a reading")
            return TalkReply(kind="failed", answer=_fixed("failed"), incident_id=incident_id)

    async def _read(
        self,
        text: str,
        snapshot: Snapshot,
        facts: IncidentFacts | None,
        turns: list[Turn],
    ) -> TalkReading:
        history = [Turn(said=t.said[:TURN_CHARS], answered=t.answered[:TURN_CHARS]) for t in turns]
        message = prompts.load(
            "talk/message",
            now=snapshot.now,
            zone=snapshot.zone,
            state=self._guarded("bleep_state", _json(snapshot), STATE_CHARS),
            incident=self._guarded("incident", _json(facts), INCIDENT_CHARS),
            history=self._guarded("conversation", _json(history), HISTORY_CHARS),
            message=self._guarded("person", text.strip(), TEXT_CHARS),
        )
        messages = [SystemMessage(prompts.load("talk/read")), HumanMessage(message)]
        config: RunnableConfig | None = None
        if self.ledger is not None:
            await check_chat_cap(self.ledger, self.budgets())
            config = {"callbacks": [UsageTracker(TALK_USAGE, self.ledger)]}
        timeout = min(TIMEOUT_S, self.budgets().model_timeout_s)
        guard = ModelGuard(timeout, retries=RETRIES, sleep=self.sleep)
        model = self.models("cheap")
        return await invoke_structured(model, TalkReading, messages, config=config, guard=guard)

    async def _runs_spent(self) -> bool:
        """Whether the day's cost cap of the incident runs is spent, so a look cannot start."""
        if self.ledger is None:
            return False
        return await self.ledger.spent_today() >= self.budgets().daily_cost_usd

    def _guarded(self, source: str, text: str, limit: int) -> str:
        wrapped, redactions = guard_text(source, text, limit)
        if redactions and self.guard_log is not None:
            self.guard_log.record(
                TALK_USAGE, "redacted", f"{redactions} secret(s) redacted from {source}"
            )
        return wrapped

    def _answer(
        self,
        reading: TalkReading,
        text: str,
        snapshot: Snapshot,
        facts: IncidentFacts | None,
        incident_id: str | None,
        spent: bool = False,
    ) -> TalkReply:
        def reply(kind: ReplyKind, answer: str, offer: Offer | None = None) -> TalkReply:
            return TalkReply(
                kind=kind,
                answer=answer,
                action=reading.action,
                incident_id=incident_id,
                offer=offer,
            )

        if reading.kind == "question" and (answer := clean_answer(reading.answer)):
            return reply("question", answer)
        if reading.kind == "refusal" and reading.refusal == "secret":
            return reply("refusal", _fixed("refused_secret"))
        if reading.kind == "action" and reading.action is not None:
            action = reading.action
            now, zone = self.clock(), self.zone()
            if action.kind == "look_again":
                checked = look_offer(action, snapshot, now, zone, spent)
            else:
                checked = check(action, snapshot, facts, now, zone, text.strip()[:TEXT_CHARS])
            if isinstance(checked, str):
                return reply("action", checked)
            return reply("action", confirm_line(checked, now, zone), checked)
        kind: ReplyKind = "refusal" if reading.kind == "refusal" else "unclear"
        line = _fixed("refused_change" if kind == "refusal" else "unclear")
        fallback = self._fallback(kind, line, text, facts, incident_id)
        return fallback.model_copy(update={"action": reading.action})

    def _fallback(
        self,
        kind: ReplyKind,
        line: str,
        text: str,
        facts: IncidentFacts | None,
        incident_id: str | None,
    ) -> TalkReply:
        """`line`, or, for a message about an open incident whose plan waits, the offer to send
        the person's words to that plan: in a reply to a waiting plan a change Bleep would
        refuse elsewhere is an instruction, and a message Bleep could not read may still be
        one. The person sees what would be sent and confirms it."""
        waiting = facts is not None and facts.plan_waiting and facts.status not in TERMINAL_STATUSES
        if facts is None or not waiting:
            return TalkReply(kind=kind, answer=line, incident_id=incident_id)
        offer = instruction(facts.id, facts.name or facts.id, text.strip()[:TEXT_CHARS])
        answer = confirm_line(offer, self.clock(), self.zone())
        if kind == "failed":  # the person learns their words were not read before sending them
            answer = f"{line}\n{answer}"
        return TalkReply(kind=kind, answer=answer, incident_id=incident_id, offer=offer)


def _asks_look(reading: TalkReading) -> bool:
    action = reading.action if reading.kind == "action" else None
    return action is not None and action.kind == "look_again"
