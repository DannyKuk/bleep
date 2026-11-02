"""Diagnose: a structured diagnosis of what was found.

One model call over Gather's context and the investigation so far. Confidence below the budget's
`low_confidence` (0.6) marks the diagnosis `unsure`; the first time that happens the node adds a
message saying what is missing and the route sends the run back to Investigate once. A second low
result goes on to Propose still `unsure`, so the proposal says "I am not sure, here is what I
found". A model that cannot produce the structure, or a spent budget, gives the same fallback
diagnosis instead of an error, built from what Gather saw.
"""

import logging
from typing import Literal

from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage
from pydantic import Field

from bleep import prompts
from bleep.graph.deps import GraphDeps
from bleep.graph.state import IncidentState, Node
from bleep.llm.structured import StructuredOutputFailed, invoke_structured
from bleep.llm.usage import BudgetExceeded, UsageTracker, check_budget
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.graph import Diagnosis, Evidence, GatherContext
from bleep.schemas.incidents import Signal, Subject

log = logging.getLogger(__name__)

DETAIL_LIMIT = 300  # characters per evidence entry
MAX_EVIDENCE = 8
MESSAGE_LIMIT = 1500  # characters of each investigation message shown to the model
MISSING_LIMIT = 300
FALLBACK_CAUSE = "I could not form a diagnosis"


class _Draft(Diagnosis):
    """What the model fills in: a diagnosis plus, when unsure, what it still needs to know."""

    missing: str = Field(default="")


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _describe_subject(subject: Subject, context: GatherContext) -> str:
    if subject.kind == "service":
        return f"the service {context.service_name or subject.service_id} on host {subject.host_id}"
    return f"the host {subject.host_id}"


def _describe_signals(signals: list[Signal]) -> str:
    lines = [
        f"- {s.kind}" + (f" ({s.container})" if s.container else "") + f" at {s.at:%H:%M:%S}"
        for s in signals
    ]
    return "\n".join(lines) or "(none recorded)"


def _describe_context(context: GatherContext) -> str:
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
    if context.basics is not None:
        parts.append(f"Host basics:\n{context.basics.model_dump_json()}")
    if context.top_memory:
        parts.append(
            "Containers using the most memory:\n"
            + "\n".join(f"- {m.name}: {m.mem_percent:.0f}%" for m in context.top_memory)
        )
    for rule in [*context.host_rules, *context.service_rules]:
        parts.append(f"Rule from the owner: {rule}")
    for past in context.similar:
        parts.append(f"Similar past incident: {past.model_dump_json()}")
    for gap in context.gaps:
        parts.append(f"Could not read: {gap}")
    return "\n\n".join(parts) or "(nothing was collected)"


def _describe_investigation(messages: list[AnyMessage]) -> str:
    lines = [
        f"[{m.type}] {_clip(m.text, MESSAGE_LIMIT)}"
        for m in messages
        if not isinstance(m, SystemMessage) and m.text
    ]
    return "\n".join(lines) or "(no investigation yet)"


def _evidence(draft: Diagnosis) -> list[Evidence]:
    """Short statements only: a model that pastes output gets it cut, never passed on whole."""
    return [
        Evidence(source=_clip(e.source, 60), detail=_clip(e.detail, DETAIL_LIMIT))
        for e in draft.evidence[:MAX_EVIDENCE]
    ]


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

        prompt = prompts.load(
            "graph/diagnose",
            subject=_describe_subject(state["subject"], context),
            signals=_describe_signals(state.get("signals", [])),
            context=_describe_context(context),
            investigation=_describe_investigation(state.get("messages", [])),
        )
        try:
            await check_budget(deps.ledger, incident_id, budgets)
            draft = await invoke_structured(
                deps.models("main"),
                _Draft,
                [HumanMessage(prompt)],
                config={"callbacks": [UsageTracker(incident_id, deps.ledger)]},
            )
        except BudgetExceeded as spent:
            log.info("diagnose: %s budget spent, no model call", spent.kind)
            return {"diagnosis": _fallback(context)}
        except StructuredOutputFailed as failed:
            log.warning("diagnose: no structured output: %s", _clip(failed.raw, 120))
            return {"diagnosis": _fallback(context)}

        missing = _clip(draft.missing, MISSING_LIMIT)
        diagnosis = Diagnosis(
            root_cause=_clip(draft.root_cause, 500),
            category=draft.category,
            evidence=_evidence(draft),
            confidence=draft.confidence,
            affected_services=draft.affected_services,
            unsure=draft.confidence < budgets.low_confidence,
        )
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
