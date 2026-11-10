"""Answer what the person writes to Bleep, from Bleep's own state, with one cheap-model call.

Both surfaces call `Talk.reply`: the Telegram bot and the dashboard's panel (#391). It
reads; it never changes anything. The person's text, the conversation, the incident it is about
and the snapshot all reach the model as untrusted data (`guard_text`), and the model only picks
a kind and, for a question, writes the answer. An action comes back as an offer, checked by code
against Bleep's state (`talk.offers`), that the surface confirms with a button; a refusal, an
unclear message, a failure and an offer that fails a check are answered with fixed lines from
`prompts/talk/`, never with the model's words (docs/design/notify.md). Every message lands in the
talk log (`talk.log`, #411) with how it was read, and with tracing on each reading is its own
trace, in the session of its surface. A message about no incident, sent within `FOLLOW_UP` of
a turn about one, is read as about that one (#422).
"""

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Any, Protocol, runtime_checkable

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langchain_core.runnables.config import merge_configs
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
    CallCost,
    CallCosts,
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
    TalkExchange,
    TalkReading,
    TalkReply,
    TalkSurface,
    TalkUsage,
    Turn,
)
from bleep.talk.links import known_links, links_in, offer_links
from bleep.talk.log import TalkLog
from bleep.talk.looks import look_offer
from bleep.talk.offers import check, confirm_line, instruction
from bleep.talk.state import StateReader

log = logging.getLogger(__name__)

TALK_USAGE = CHAT_USAGE  # the usage ledger's key for chat answers, with a daily cap of its own
TEXT_CHARS = 1000  # the person's message, as the model reads it
TURN_CHARS = 600  # each side of an earlier turn
STATE_CHARS = 16_000  # the snapshot; its tail is kept if a large setup outgrows it
INCIDENT_CHARS = 8000  # the incident in detail, its run's checks included (#422)
HISTORY_CHARS = 8000  # the turns a surface keeps (`talk.history.TURNS_KEPT`), each side capped
ANSWER_CHARS = 1500  # an answer Bleep sends, well under Telegram's 4,096
# A chat answer must not hold the person for long: one call of at most this long, one retry,
# and the whole reading (a JSON fallback and its retry included) cut off after REPLY_TIMEOUT_S.
TIMEOUT_S = 30
RETRIES = 1
REPLY_TIMEOUT_S = 60
# A message that is not a reply goes on about the incident the conversation's last turn was
# about while that turn is this recent: "how did you check it?" after a look (#422).
FOLLOW_UP = timedelta(minutes=10)
# A link in an answer: a scheme, `www.`, or a host name followed by a path. Telegram makes them
# clickable, and a name or a cause from a host could carry one.
_LINK = re.compile(
    r"\b(?:[a-z][a-z0-9+.-]*://|www\.)\S+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)+/\S*", re.IGNORECASE
)
ModelFactory = Callable[[Role], BaseChatModel]


def _json(value: BaseModel | list[Turn] | None) -> str:
    if isinstance(value, list):
        said = [t.model_dump(include={"said", "answered"}) for t in value]
        return json.dumps(said, ensure_ascii=False, indent=1)
    if value is None:
        return "null"
    # Compact, and without the fields that only say their default: the snapshot of a large setup
    # stays well inside STATE_CHARS. One host, service or incident a line: the guard cuts a line
    # over 4,000 characters in its middle (`guard.sanitize.cap_lines`), and on one line a
    # snapshot of eleven services lost the ones in the middle (#410).
    data = value.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    return "{\n" + ",\n".join(f"{_compact(k)}:{_items(v)}" for k, v in data.items()) + "\n}"


def _compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _items(value: object) -> str:
    """A list one item a line; anything else on the line of its key."""
    if isinstance(value, list) and value:
        return "[\n" + ",\n".join(_compact(item) for item in value) + "\n]"
    return _compact(value)


def _fixed(name: str, language: str, /, **values: str) -> str:
    return prompts.load_in(language, f"talk/{name}", **values).strip()


def clean_answer(text: str, language: str = "en") -> str:
    """The model's answer as a chat may show it: no control characters, secrets redacted, no
    links (each said removed, in `language`), capped."""
    clean = redact_text(strip_control(text)).strip()
    clean = _LINK.sub(_fixed("link_removed", language), clean)
    return clean if len(clean) <= ANSWER_CHARS else clean[: ANSWER_CHARS - 1].rstrip() + "…"


def logged_text(text: str) -> str:
    """The person's words as the talk log keeps them: as the model read them, never raw."""
    return _logged_words(text, TEXT_CHARS)


def follow_up(turns: Sequence[Turn], now: datetime) -> str | None:
    """The incident a message that names none goes on about: the one the conversation's last
    turn was about, while that turn is at most `FOLLOW_UP` old. Only the last turn counts, so a
    turn about nothing, or about another incident, ends it."""
    last = turns[-1] if turns else None
    if last is None or last.incident_id is None or last.at is None:
        return None
    return last.incident_id if now - last.at <= FOLLOW_UP else None


def _no_zone() -> tzinfo | None:
    return None


def _english() -> str:
    return "en"


@runtime_checkable
class Tracing(Protocol):
    """Traces a reading (`llm.tracing`): each is its own trace, in its surface's session."""

    def talk_config(self, surface: TalkSurface) -> RunnableConfig: ...


@dataclass
class _Seen:
    """What happened inside one reply, for its entry in the log."""

    reading: TalkReading | None = None
    error: str | None = None
    checked: Offer | str | None = None


class Talk:
    """`models` builds the chat model for a role (the `cheap` one is used); `budgets` gives the
    chat's own daily cost cap and the model timeout, `ledger` records what each call cost.
    `zone` gives Bleep's time zone at each message (None: UTC), in which a snooze's time is read
    and said, and `language` the language of the fixed lines it answers with; the model's own
    answer is in whatever it wrote. Each message is kept in `log`; `tracer` traces each
    reading."""

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
        log: TalkLog | None = None,
        tracer: Tracing | None = None,
        language: Callable[[], str] = _english,
    ) -> None:
        self.language = language
        self.state = state
        self.models = models
        self.budgets = budgets
        self.ledger = ledger
        self.guard_log = guard_log
        self.sleep = sleep
        self.zone = zone
        self.clock = clock
        self.log = log if log is not None else TalkLog(clock)
        self.tracer = tracer

    async def reply(
        self,
        text: str,
        incident_id: str | None = None,
        turns: Sequence[Turn] = (),
        *,
        surface: TalkSurface,
    ) -> TalkReply:
        """Read `text`, said about the incident `incident_id` if any after the earlier `turns`
        of this conversation on `surface`, and say what Bleep answers. Never raises for a model
        that fails: that is the `failed` reply, which still offers to send the words to a plan
        that waits on the incident, as a reply to it could before. The message and its reading
        go into the log; the reply carries the entry's id, for the surface to add what it
        showed (`TalkLog.shown`). Without `incident_id`, a message right after a turn about an
        incident is about that one (`follow_up`); the reply's `incident_id` says which, for the
        surface to keep with the turn."""
        at, seen, costs = self.clock(), _Seen(), CallCosts()
        incident_id = incident_id or follow_up(turns, at)
        said = await self._reply(text, incident_id, turns, surface, seen, costs)
        exchange = TalkExchange(
            id=self.log.next_id(),
            at=at,
            surface=surface,
            text=logged_text(text),
            incident_id=incident_id,
            kind=said.kind,
            reading=_logged_reading(seen.reading),
            error=seen.error,
            offer=_logged_offer(said.offer),
            refused=seen.checked if isinstance(seen.checked, str) else None,
            answer=said.answer,
            links=said.links,
            usage=_usage(costs.calls),
        )
        self.log.add(exchange)
        return said.model_copy(update={"exchange_id": exchange.id})

    async def _reply(
        self,
        text: str,
        incident_id: str | None,
        turns: Sequence[Turn],
        surface: TalkSurface,
        seen: _Seen,
        costs: CallCosts,
    ) -> TalkReply:
        facts: IncidentFacts | None = None
        language = self.language()
        try:
            async with asyncio.timeout(REPLY_TIMEOUT_S):
                snapshot = await self.state.snapshot()
                facts = await self.state.incident(incident_id) if incident_id else None
                reading = await self._read(text, snapshot, facts, list(turns), surface, costs)
                seen.reading = reading
                spent = _asks_look(reading) and await self._runs_spent()
        except BudgetExceeded as error:
            seen.error = type(error).__name__
            line = _fixed("spent", language)
            return self._fallback("failed", line, text, facts, incident_id, language)
        except Exception as error:  # the type only: a provider's message may quote its key
            seen.error = type(error).__name__
            log.warning("talk: could not read a message (%s)", seen.error)
            line = _fixed("failed", language)
            return self._fallback("failed", line, text, facts, incident_id, language)
        try:
            said = self._answer(
                reading, text, snapshot, facts, incident_id, spent, seen, language=language
            )
            named = offer_links(said.offer) if said.offer else []
            candidates = [*named, *known_links(snapshot, facts)]
            return said.model_copy(update={"links": links_in(said.answer, candidates)})
        except Exception as error:  # a bug checking a reading must still answer, and its trace
            seen.error = type(error).__name__
            log.exception("talk: could not answer a reading")
            answer = _fixed("failed", language)
            return TalkReply(kind="failed", answer=answer, incident_id=incident_id)

    async def _read(
        self,
        text: str,
        snapshot: Snapshot,
        facts: IncidentFacts | None,
        turns: list[Turn],
        surface: TalkSurface,
        costs: CallCosts,
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
        callbacks: list[Any] = [costs]
        if self.ledger is not None:
            await check_chat_cap(self.ledger, self.budgets())
            callbacks.append(UsageTracker(TALK_USAGE, self.ledger))
        config = merge_configs(self._traced(surface), {"callbacks": callbacks})
        timeout = min(TIMEOUT_S, self.budgets().model_timeout_s)
        guard = ModelGuard(timeout, retries=RETRIES, sleep=self.sleep)
        model = self.models("cheap")

        async def structured(asked: list[BaseMessage], config: RunnableConfig) -> TalkReading:
            return await invoke_structured(model, TalkReading, asked, config=config, guard=guard)

        # One parent run, so the tool attempt, a retry and the JSON fallback nest under one trace
        # instead of each starting its own.
        reading = RunnableLambda(structured, name=f"talk-{surface}")
        return await reading.ainvoke(messages, config)

    def _traced(self, surface: TalkSurface) -> RunnableConfig:
        """The tracer's part of the call's config; a tracer that fails leaves the call untraced."""
        if self.tracer is None:
            return {}
        try:
            return self.tracer.talk_config(surface)
        except Exception as error:  # by type only: its message may quote an endpoint or a key
            log.warning("talk: tracing failed (%s); reading untraced", type(error).__name__)
            return {}

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
        seen: _Seen | None = None,
        *,
        language: str,
    ) -> TalkReply:
        def reply(kind: ReplyKind, answer: str, offer: Offer | None = None) -> TalkReply:
            return TalkReply(
                kind=kind,
                answer=answer,
                action=reading.action,
                incident_id=incident_id,
                offer=offer,
            )

        if reading.kind == "question" and (answer := clean_answer(reading.answer, language)):
            return reply("question", answer)
        if reading.kind == "refusal" and reading.refusal == "secret":
            return reply("refusal", _fixed("refused_secret", language))
        if reading.kind == "action" and reading.action is not None:
            action = reading.action
            now, zone = self.clock(), self.zone()
            if action.kind == "look_again":
                checked = look_offer(action, snapshot, now, zone, spent, language)
            else:
                words = text.strip()[:TEXT_CHARS]
                checked = check(action, snapshot, facts, now, zone, words, language)
            if seen is not None:
                seen.checked = checked
            if isinstance(checked, str):
                return reply("action", checked)
            return reply("action", confirm_line(checked, now, zone, language), checked)
        kind: ReplyKind = "refusal" if reading.kind == "refusal" else "unclear"
        line = _fixed("refused_change" if kind == "refusal" else "unclear", language)
        fallback = self._fallback(kind, line, text, facts, incident_id, language)
        return fallback.model_copy(update={"action": reading.action})

    def _fallback(
        self,
        kind: ReplyKind,
        line: str,
        text: str,
        facts: IncidentFacts | None,
        incident_id: str | None,
        language: str,
    ) -> TalkReply:
        """`line`, or, for a message about an open incident whose plan waits, the offer to send
        the person's words to that plan: in a reply to a waiting plan a change Bleep would
        refuse elsewhere is an instruction, and a message Bleep could not read may still be
        one. The person sees what would be sent and confirms it."""
        waiting = facts is not None and facts.plan_waiting and facts.status not in TERMINAL_STATUSES
        if facts is None or not waiting:
            return TalkReply(kind=kind, answer=line, incident_id=incident_id)
        offer = instruction(facts.id, facts.name or facts.id, text.strip()[:TEXT_CHARS])
        answer = confirm_line(offer, self.clock(), self.zone(), language)
        if kind == "failed":  # the person learns their words were not read before sending them
            answer = f"{line}\n{answer}"
        return TalkReply(kind=kind, answer=answer, incident_id=incident_id, offer=offer)


def _logged_reading(reading: TalkReading | None) -> TalkReading | None:
    """The reading as the log keeps it: the model's own words cleaned as an answer is, the
    links left in, since they may be what went wrong."""
    if reading is None:
        return None
    action = reading.action
    if action is not None:
        said = {"subject": action.subject, "until": action.until, "incident": action.incident}
        cleaned = {k: _logged_words(v, TURN_CHARS) for k, v in said.items() if v is not None}
        action = action.model_copy(update=cleaned)
    answer = _logged_words(reading.answer, ANSWER_CHARS)
    return reading.model_copy(update={"answer": answer, "action": action})


def _logged_words(text: str, limit: int) -> str:
    return redact_text(strip_control(text)).strip()[:limit]


def _logged_offer(offer: Offer | None) -> Offer | None:
    """The offer as the log keeps it: an instruction's words as the person's text is kept."""
    if offer is None or offer.text is None:
        return offer
    return offer.model_copy(update={"text": logged_text(offer.text)})


def _usage(calls: list[CallCost]) -> TalkUsage | None:
    if not calls:
        return None
    priced = [c.usd for c in calls if c.usd is not None]
    return TalkUsage(
        model=calls[-1].model,
        calls=len(calls),
        tokens_in=sum(c.tokens_in for c in calls),
        tokens_out=sum(c.tokens_out for c in calls),
        usd=sum(priced) if priced else None,
    )


def _asks_look(reading: TalkReading) -> bool:
    action = reading.action if reading.kind == "action" else None
    return action is not None and action.kind == "look_again"
