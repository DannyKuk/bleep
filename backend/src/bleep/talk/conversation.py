"""Answer what the person writes to Bleep, from Bleep's own state, with one cheap-model call.

Both surfaces call `Talk.reply`: the Telegram bot now, the dashboard's panel later (#391). It
reads; it never changes anything. The person's text, the conversation, the incident it is about
and the snapshot all reach the model as untrusted data (`guard_text`), and the model only picks
a kind and, for a question, writes the answer. A refusal, an unclear message, a failure and an
action Bleep cannot take from chat yet are answered with fixed lines from `prompts/talk/`, the
last built from the structured action, never from the model's words (docs/design/notify.md).
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

from bleep import prompts
from bleep.guard.log import GuardLogger
from bleep.guard.pipeline import guard_text
from bleep.guard.redact import redact_text
from bleep.guard.sanitize import strip_control
from bleep.guard.titles import title_name
from bleep.llm.factory import Role
from bleep.llm.guard import ModelGuard
from bleep.llm.structured import invoke_structured
from bleep.llm.usage import CostLedger, UsageTracker, check_daily_cap
from bleep.schemas.settings import Budgets
from bleep.schemas.talk import (
    IncidentFacts,
    ReplyKind,
    Snapshot,
    TalkAction,
    TalkReading,
    TalkReply,
    Turn,
)
from bleep.talk.state import StateReader

log = logging.getLogger(__name__)

TALK_USAGE = "talk"  # the usage ledger's key for chat answers, which have no incident
TEXT_CHARS = 1000  # the person's message, as the model reads it
TURN_CHARS = 600  # each side of an earlier turn
STATE_CHARS = 16_000  # the snapshot; its tail is kept if a large setup outgrows it
INCIDENT_CHARS = 4000
HISTORY_CHARS = 8000  # the turns a surface keeps (`talk.history.TURNS_KEPT`), each side capped
ANSWER_CHARS = 1500  # an answer Bleep sends, well under Telegram's 4,096
# A chat answer must not hold the person for long: one call of at most this long, one retry.
TIMEOUT_S = 30
RETRIES = 1
_SUBJECT_KINDS = ("snooze", "leave_alone", "watch_again", "look_again")

ModelFactory = Callable[[Role], BaseChatModel]


def _json(value: BaseModel | list[Turn] | None) -> str:
    if isinstance(value, list):
        return json.dumps([t.model_dump() for t in value], ensure_ascii=False, indent=1)
    if value is None:
        return "null"
    return value.model_dump_json(indent=1, exclude_none=True)


def _fixed(name: str, **values: str) -> str:
    return prompts.load(f"talk/{name}", **values).strip()


def clean_answer(text: str) -> str:
    """The model's answer as a chat may show it: no control characters, secrets redacted,
    capped."""
    clean = redact_text(strip_control(text)).strip()
    return clean if len(clean) <= ANSWER_CHARS else clean[: ANSWER_CHARS - 1].rstrip() + "…"


class Talk:
    """`models` builds the chat model for a role (the `cheap` one is used); `budgets` gives the
    day's cost cap and the model timeout, `ledger` records what each call cost."""

    def __init__(
        self,
        state: StateReader,
        models: ModelFactory,
        *,
        budgets: Callable[[], Budgets] = Budgets,
        ledger: CostLedger | None = None,
        guard_log: GuardLogger | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.state = state
        self.models = models
        self.budgets = budgets
        self.ledger = ledger
        self.guard_log = guard_log
        self.sleep = sleep

    async def reply(
        self, text: str, incident_id: str | None = None, turns: Sequence[Turn] = ()
    ) -> TalkReply:
        """Read `text`, said about the incident `incident_id` if any after the earlier `turns`
        of this conversation, and say what Bleep answers. Never raises for a model that fails:
        that is the `failed` reply."""
        try:
            snapshot = await self.state.snapshot()
            facts = await self.state.incident(incident_id) if incident_id else None
            reading = await self._read(text, snapshot, facts, list(turns))
        except Exception as error:  # the type only: a provider's message may quote its key
            log.warning("talk: could not read a message (%s)", type(error).__name__)
            return TalkReply(kind="failed", answer=_fixed("failed"), incident_id=incident_id)
        return self._answer(reading, snapshot, facts, incident_id)

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
            state=self._guarded("bleep_state", _json(snapshot), STATE_CHARS),
            incident=self._guarded("incident", _json(facts), INCIDENT_CHARS),
            history=self._guarded("conversation", _json(history), HISTORY_CHARS),
            message=self._guarded("person", text.strip(), TEXT_CHARS),
        )
        messages = [SystemMessage(prompts.load("talk/read")), HumanMessage(message)]
        config: RunnableConfig | None = None
        if self.ledger is not None:
            await check_daily_cap(self.ledger, self.budgets())
            config = {"callbacks": [UsageTracker(TALK_USAGE, self.ledger)]}
        timeout = min(TIMEOUT_S, self.budgets().model_timeout_s)
        guard = ModelGuard(timeout, retries=RETRIES, sleep=self.sleep)
        model = self.models("cheap")
        return await invoke_structured(model, TalkReading, messages, config=config, guard=guard)

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
        snapshot: Snapshot,
        facts: IncidentFacts | None,
        incident_id: str | None,
    ) -> TalkReply:
        def reply(kind: ReplyKind, answer: str, action: TalkAction | None = None) -> TalkReply:
            return TalkReply(kind=kind, answer=answer, action=action, incident_id=incident_id)

        if reading.kind == "question" and (answer := clean_answer(reading.answer)):
            return reply("question", answer)
        if reading.kind == "refusal":
            refusal = "refused_secret" if reading.refusal == "secret" else "refused_change"
            return reply("refusal", _fixed(refusal))
        if reading.kind == "action" and reading.action is not None:
            understood = _understood(reading.action, snapshot, facts)
            return reply("action", _fixed("not_yet", understood=understood), reading.action)
        return reply("unclear", _fixed("unclear"))


def _understood(action: TalkAction, snapshot: Snapshot, facts: IncidentFacts | None) -> str:
    """What Bleep read the action as, in its own words: the subject and the incident only as
    Bleep's state names them, never as the model wrote them."""
    if action.kind in _SUBJECT_KINDS:
        name = subject_name(snapshot, action.subject)
        return _fixed(f"understood_{action.kind}", subject=name or _fixed("unknown_subject"))
    name = incident_name(snapshot, facts, action.incident)
    return _fixed(f"understood_{action.kind}", incident=name or _fixed("unknown_incident"))


def _same(a: str, b: str) -> bool:
    return " ".join(a.lower().split()) == " ".join(b.lower().split())


def subject_name(snapshot: Snapshot, said: str | None) -> str | None:
    """The service or host `said` names, as Bleep shows it; None when Bleep knows none."""
    if not said:
        return None
    for service in snapshot.services:
        if _same(said, service.name) or _same(said, service.id):
            return title_name(service.name)
    for host in snapshot.hosts:
        if _same(said, host.id) or _same(said, host.name):
            return host.id
    return None


def incident_name(snapshot: Snapshot, facts: IncidentFacts | None, said: str | None) -> str | None:
    """The incident `said` names (a short name or an id), else the one the message is about."""
    known = [*([facts] if facts is not None else []), *snapshot.open_incidents]
    known += snapshot.ended_last_24h
    if said:
        for incident in known:
            if _same(said, incident.id) or (incident.name and _same(said, incident.name)):
                return incident.name or incident.id
        return None
    if facts is not None:
        return facts.name or facts.id
    return None
