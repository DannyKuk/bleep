"""Diagnose: a structured diagnosis of what was found.

One model call over Gather's context and the investigation so far. Confidence below the budget's
`low_confidence` (0.6) marks the diagnosis `unsure`; the first time that happens the node adds a
message saying what is missing and the route sends the run back to Investigate once. A second low
result goes on to Propose still `unsure`, so the proposal says "I am not sure, here is what I
found". A model that cannot produce the structure, or a spent budget, gives the same fallback
diagnosis instead of an error, built from what Gather saw.

The model's `planted_in` is kept only for places the run read and is never shown
(`graph/planted.py`, #105). When host text the run read holds a wrapper tag the guard escaped or a
line that gives orders (`guard/orders.py`, #271), Bleep adds one evidence line of its own that
names where and quotes nothing. That line and the fallback's cause are stored in English, which is
what Propose and the report read, and keep the templates they are made of, to show them in the
person's language (#455).

The root cause and each evidence statement keep a commit sha only when the run read that commit
from GitHub (`graph/commits.py`, #350), since the approval message shows them before any report;
a root cause left with nothing else is the fallback's, and such a statement is dropped.
"""

import asyncio
import logging
import typing
from typing import Literal

from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import Field, field_validator

from bleep import prompts
from bleep.graph.commits import ShaCheck, read_shas
from bleep.graph.deps import GraphDeps
from bleep.graph.host_text import describe_basics, describe_urls, service_name
from bleep.graph.language import language_rule
from bleep.graph.look import look_rule
from bleep.graph.planted import (
    EVIDENCE_SOURCE,
    planted_places,
    planted_sentence,
    planted_shown,
    read_sources,
)
from bleep.graph.state import IncidentState, Node
from bleep.guard.log import GuardKind
from bleep.llm.structured import StructuredOutputFailed, invoke_structured
from bleep.llm.usage import BudgetExceeded, UsageTracker, check_budget, tracked
from bleep.memory.text import describe_fact, describe_past
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.graph import Diagnosis, Evidence, GatherContext, PlantedSource
from bleep.schemas.incidents import Signal, Subject
from bleep.schemas.shown import Shown

log = logging.getLogger(__name__)

DETAIL_LIMIT = 300  # characters per evidence entry
MAX_EVIDENCE = 8
MESSAGE_LIMIT = 1500  # characters of each investigation message but a tool result
MISSING_LIMIT = 300
FALLBACK_CAUSE = "I could not form a diagnosis"
FALLBACK_SHOWN = Shown.of("diagnosis/fallback_cause")


SOURCES: tuple[PlantedSource, ...] = typing.get_args(PlantedSource)


class _Draft(Diagnosis):
    """What the model fills in: a diagnosis plus, when unsure, what it still needs to know.
    `planted_in` takes any text here, so one word off the list does not fail the whole answer;
    `_planted` maps it onto the list."""

    missing: str = Field(default="")
    planted_in: list[str] = Field(default_factory=list)  # type: ignore[assignment]

    @field_validator("planted_in", mode="before")
    @classmethod
    def _as_list(cls, value: object) -> object:
        """A weak model's null is none, and a bare word is a list of one."""
        if value is None:
            return []
        return [value] if isinstance(value, str) else value


def _planted(said: list[str]) -> list[PlantedSource]:
    """The sources the model named, on the closed list, in its order; anything else is `other`."""
    found = {s if s in SOURCES else "other" for s in (x.strip().lower() for x in said)}
    return [s for s in SOURCES if s in found]


async def _with_planted(
    diagnosis: Diagnosis, context: GatherContext, messages: list[AnyMessage], subject: Subject
) -> Diagnosis:
    """The diagnosis with `planted_in` cut to the places the run read, and Bleep's own evidence
    line when code saw planted orders (`graph/planted.planted_places`, read in a worker thread, as
    hostile output makes it slow, #314). Neither changes what Bleep does next."""
    read = read_sources(context, messages)
    planted_in = [s for s in diagnosis.planted_in if s in read]
    found = diagnosis.model_copy(update={"planted_in": planted_in})
    places = await asyncio.to_thread(planted_places, context, messages, subject)
    sentence = planted_sentence(places)
    if sentence is not None:
        line = Evidence(source=EVIDENCE_SOURCE, detail=sentence, shown=planted_shown(places))
        found = found.model_copy(update={"evidence": [*found.evidence, line]})
    return found


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _describe_subject(subject: Subject, context: GatherContext) -> str:
    if subject.kind == "service":
        return f"the service {service_name(subject, context)} on host {subject.host_id}"
    return f"the host {subject.host_id}"


def _describe_signals(signals: list[Signal]) -> str:
    lines = [
        f"- {s.kind}" + (f" ({s.container})" if s.container else "") + f" at {s.at:%H:%M:%S}"
        for s in signals
    ]
    return "\n".join(lines) or "(none recorded)"


def _describe_context(context: GatherContext, with_urls: bool = True) -> str:
    """What Gather collected. Its URL checks are left out after a fix ran (`with_urls` False):
    they are from before it, and Verify's checks say how the URLs are now."""
    parts: list[str] = []
    if context.containers:
        parts.append(
            "Containers:\n"
            + "\n".join(
                f"- {c.name}: {c.state}"
                + (f", exit code {c.exit_code}" if c.exit_code is not None else "")
                for c in context.containers
            )
        )
    for name, text in context.logs.items():
        parts.append(f"Recent log of {name}:\n{text}")
    if context.urls and with_urls:
        checks = describe_urls(context.urls)
        parts.append(prompts.load("graph/url_checks_diagnose", checks=checks).strip())
    elif context.no_urls and with_urls:
        parts.append(prompts.load("graph/url_checks_none_diagnose").strip())
    if context.basics is not None:
        parts.append("Host basics:\n" + "\n".join(describe_basics(context.basics)))
    if context.top_memory:
        parts.append(
            "Containers using the most memory:\n"
            + "\n".join(f"- {m.name}: {m.mem_percent:.0f}%" for m in context.top_memory)
        )
    for rule in [*context.host_rules, *context.service_rules]:
        parts.append(f"Rule from the owner: {rule}")
    for past in context.similar:
        parts.append(f"Similar past incident (Bleep's own record): {describe_past(past)}")
    for fact in context.facts:
        parts.append(f"Fact about this service (Bleep's own record): {describe_fact(fact)}")
    for gap in context.gaps:
        parts.append(f"Could not read: {gap}")
    return "\n\n".join(parts) or "(nothing was collected)"


def _describe_investigation(messages: list[AnyMessage]) -> str:
    lines = [
        f"[{m.type}] {_message_text(m)}"
        for m in messages
        if not isinstance(m, SystemMessage) and m.text
    ]
    return "\n".join(lines) or "(no investigation yet)"


def _message_text(message: AnyMessage) -> str:
    """A tool result as Investigate read it, since the guard already cut it to size and wrapped
    it once: a cut here dropped its closing tag, and with it a value past the cut, such as an env
    line after a long list of labels (#561). Any other message is cut to `MESSAGE_LIMIT`."""
    if isinstance(message, ToolMessage):
        return message.text
    return _clip(message.text, MESSAGE_LIMIT)


def _evidence(draft: Diagnosis, shas: ShaCheck) -> list[Evidence]:
    """Short statements only: a model that pastes output gets it cut, never passed on whole. A
    statement left empty by a sha the run never read is dropped."""
    entries = [
        Evidence(source=_clip(e.source, 60), detail=shas(_clip(e.detail, DETAIL_LIMIT)))
        for e in draft.evidence[:MAX_EVIDENCE]
    ]
    return [e for e in entries if e.detail]


def _fallback(context: GatherContext | None) -> Diagnosis:
    """No diagnosis: say so, and keep what Gather saw as evidence for the human."""
    evidence: list[Evidence] = []
    if context is not None:
        for c in context.containers:
            if c.state != "running":
                evidence.append(Evidence(source="gather", detail=_clip(_state(c), DETAIL_LIMIT)))
        for gap in context.gaps:
            evidence.append(Evidence(source="gather", detail=_clip(f"could not read: {gap}", 300)))
    return Diagnosis(
        root_cause=FALLBACK_CAUSE,
        shown=FALLBACK_SHOWN,
        category="unknown",
        evidence=evidence[:MAX_EVIDENCE],
        confidence=0.0,
        unsure=True,
    )


def _state(container: ContainerSummary) -> str:
    code = f" with exit code {container.exit_code}" if container.exit_code is not None else ""
    return f"container {container.name} is {container.state}{code}"


def make(deps: GraphDeps) -> Node:
    async def diagnose(state: IncidentState) -> dict:
        incident_id = state["incident_id"]
        context = state.get("context")
        budgets = deps.settings_store.budgets()
        rounds = state.get("investigate_rounds", 0)
        if context is None:
            context = GatherContext(at=deps.clock())
        messages, subject = state.get("messages", []), state["subject"]

        prompt = prompts.load(
            "graph/diagnose",
            subject=_describe_subject(subject, context),
            signals=_describe_signals(state.get("signals", [])),
            look=look_rule("diagnose", state.get("signals", [])),
            language=language_rule("graph/diagnose", deps.settings_store.language()),
            context=_describe_context(context, with_urls=not state.get("verify_loops")),
            investigation=_describe_investigation(messages),
            untrusted_rule=prompts.load("untrusted/diagnose").strip(),
            planted_in_rule=prompts.load("untrusted/diagnose_planted_in").strip(),
        )
        try:
            await check_budget(deps.ledger, incident_id, budgets)
            draft = await invoke_structured(
                deps.models("main"),
                _Draft,
                [HumanMessage(prompt)],
                config=tracked(UsageTracker(incident_id, deps.ledger)),
                guard=deps.guard(),
            )
        except BudgetExceeded as spent:
            log.info("diagnose: %s budget spent, no model call", spent.kind)
            fallback = _fallback(context)
            return {"diagnosis": await _with_planted(fallback, context, messages, subject)}
        except StructuredOutputFailed as failed:
            log.warning("diagnose: no structured output: %s", _clip(failed.raw, 120))
            fallback = _fallback(context)
            return {"diagnosis": await _with_planted(fallback, context, messages, subject)}

        missing = _clip(draft.missing, MISSING_LIMIT)
        shas = ShaCheck(read_shas(messages))
        root_cause = shas(_clip(draft.root_cause, 500))
        if root_cause:
            diagnosis = Diagnosis(
                root_cause=root_cause,
                category=draft.category,
                evidence=_evidence(draft, shas),
                confidence=draft.confidence,
                affected_services=draft.affected_services,
                unsure=draft.confidence < budgets.low_confidence,
                planted_in=_planted(draft.planted_in),
            )
        else:
            # The cause was only a made-up sha: no diagnosis, as when the model gave none, but
            # what the model saw planted still counts.
            diagnosis = _fallback(context).model_copy(
                update={"planted_in": _planted(draft.planted_in)}
            )

        def record(kind: GuardKind, detail: str) -> None:
            if deps.guard_log is not None:
                deps.guard_log.record(incident_id, kind, detail)

        shas.record_to(record, "the diagnosis")
        diagnosis = await _with_planted(diagnosis, context, messages, subject)
        update: dict = {"diagnosis": diagnosis}
        if diagnosis.unsure and rounds == 1:
            update["messages"] = [
                HumanMessage(
                    prompts.load(
                        "graph/diagnose_missing",
                        root_cause=diagnosis.root_cause,
                        missing=missing or "anything that separates the possible causes",
                    )
                )
            ]
        return update

    return diagnose


def route(state: IncidentState) -> Literal["investigate", "propose"]:
    """An unsure diagnosis after the first round goes back to Investigate once; then Propose."""
    diagnosis = state.get("diagnosis")
    if diagnosis is not None and diagnosis.unsure and state.get("investigate_rounds", 0) == 1:
        return "investigate"
    return "propose"
