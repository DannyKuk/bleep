"""Report: close the run with its outcome and a written report.

The outcome comes first, in code: one set earlier (a spent budget, Verify's `resolved`) stands,
otherwise it follows the decision, and an approved plan that nothing verified is `unverified`,
never `resolved`. The report around it is mostly Bleep's own: the title, the fixed outcome text,
the action taken (from `step_results`), the human's decision with its channel, and a timeline
built from the incident's stored bus events. One model call writes the summary, the cause and the
follow-ups from that record, without any log text; what it writes is cut to size, loses every
sentence that says "fixed" unless the outcome reads "Fixed and verified", and goes through
`guard.report_check` against the logs the run read. A model that fails or a spent budget gives a
report written from the record alone. The report is published as a `report` event, sent through
`Notifier.report` for every outcome, remembered in long-term memory with the human's decision
(`Memory.add_report`). The node does not close the incident: the runner does, once the
report is in the checkpoint, so a crash before that leaves the incident open and the next boot
runs the node again rather than leaving a closed incident with no stored report.

When host text tried to give Bleep orders, code adds a fixed follow-up that names where, from a
closed list, and quotes nothing, and sets `Report.planted_in` to those places (#105): a wrapper tag
the guard escaped, or the diagnosis's `planted_in` for a place the run read (`graph/planted.py`).

When the human gave instructions on a service incident, a second model call turns the lasting
ones ("always redeploy rather than restart") into at most two service facts
(`Memory.replace_report_facts`). That call sees only the human's words, the plan and the service's
name, never a diagnosis or a log, so nothing a host wrote can become a fact Bleep trusts later.
"""

import logging
import re

from langchain_core.messages import HumanMessage, ToolMessage
from pydantic import BaseModel, Field

from bleep import prompts
from bleep.graph.deps import GraphDeps
from bleep.graph.planted import planted_places, planted_sentence
from bleep.graph.state import IncidentState, Node, plan_results
from bleep.guard.log import GuardKind
from bleep.guard.report_check import check_report
from bleep.llm.structured import StructuredOutputFailed, invoke_structured
from bleep.llm.usage import BudgetExceeded, UsageTracker, check_budget, tracked
from bleep.schemas.graph import (
    FACT_CHARS,
    Decision,
    Diagnosis,
    Outcome,
    PlantedPlace,
    Proposal,
    Report,
    StepResult,
)
from bleep.schemas.graph import TimelineEntry as Entry
from bleep.schemas.incidents import Incident, IncidentEvent, Signal

log = logging.getLogger(__name__)

# What the report says for each outcome; only `resolved` may ever say fixed (see `outcome_text`).
OUTCOME_TEXT: dict[str, str] = {
    "resolved": "Fixed and verified",
    "unverified": "Action taken, not verified",
    "rejected": "Rejected, nothing was done",
    "no_action": "No action proposed, nothing was done",
    "budget": "Stopped, the model budget is spent",
    "replan_limit": "Stopped after too many new plans",
}
# `resolved` when the checks passed but no step of the plan succeeded: the service came back on its
# own (a restart policy, Coolify), so it is not "Fixed".
HEALTHY_ANYWAY = "Healthy again (verified); no step of the plan succeeded"
# Added to an early stop (budget, re-plans) after Verify sent back a run in which a step ran.
NOT_VERIFIED = "an earlier action was not verified"
CHANNEL = {"telegram": "Telegram", "dashboard": "the dashboard"}
FINISHED = {"gather": "Gathered the context", "diagnose": "Diagnosed"}
SUMMARY_CHARS = 800
CAUSE_CHARS = 500
FOLLOW_UPS = 5
FOLLOW_UP_CHARS = 300
ENTRY_CHARS = 200
NOT_DETERMINED = "Not determined."
MAX_FACTS = 2

_FIXED = re.compile(r"\bfixed\b", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class _Draft(BaseModel):
    """What the model writes; a missing field is filled from Bleep's own record."""

    summary: str = ""
    cause: str = ""
    follow_ups: list[str] = Field(default_factory=list)


class _Facts(BaseModel):
    """The lasting facts the model found in the human's instructions."""

    facts: list[str] = Field(default_factory=list)


def outcome(state: IncidentState) -> Outcome:
    """An outcome set earlier (a spent budget) stands; otherwise anything that ran is
    `unverified`, and the rest follows the decision."""
    if state.get("outcome"):
        return state["outcome"]  # type: ignore[return-value]
    if any(r.reached_engine for r in state.get("step_results") or []):
        return "unverified"  # a later plan was rejected or empty, but an earlier one ran
    decision = state.get("decision")
    proposal = state.get("proposal")
    if decision is not None and decision.kind == "reject" and not (proposal and proposal.refused):
        return "rejected"
    if proposal is None or proposal.kind == "no_action":
        return "no_action"
    return "unverified"  # approved, but nothing ran or was checked: never claim "fixed"


def outcome_text(
    result: Outcome, results: list[StepResult] | None, proposal: Proposal | None
) -> str:
    """The outcome's fixed text, told apart where the outcome alone would mislead: a pass with no
    step of this plan that succeeded (`plan_results`: an earlier plan's success is not this
    plan's), and an early stop after any action, of this plan or an earlier one, that Verify
    could not confirm."""
    results = results or []
    if result == "resolved" and not any(r.ok for r in plan_results(proposal, results)):
        return HEALTHY_ANYWAY
    if result in ("budget", "replan_limit") and any(r.reached_engine for r in results):
        return f"{OUTCOME_TEXT[result]}; {NOT_VERIFIED}"
    return OUTCOME_TEXT[result]


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _no_fixed_claim(text: str) -> str:
    """`text` without the sentences that say "fixed"."""
    sentences = _SENTENCE_END.split(text.strip())
    return " ".join(s for s in sentences if s and not _FIXED.search(s))


def decision_text(
    kind: str, via: str | None, text: str | None = None, *, let_go: bool = False
) -> str:
    """The decision as one line, with its channel: "Approved on Telegram". `let_go` is a reject
    of a plan Bleep's own checks refused."""
    where = CHANNEL.get(via or "", "a channel")
    if kind == "approve":
        return f"Approved on {where}"
    if kind == "reject":
        return f"Let go on {where}" if let_go else f"Rejected on {where}"
    return f"Instruction from {where}: {_clip(text or '', ENTRY_CHARS)}"


def _steps_text(steps: list[dict]) -> str:
    return "; ".join(
        f"{s.get('action', '?')} {s.get('target', '?')} on {s.get('host_id', '?')}" for s in steps
    )


def _signal_text(signal: Signal) -> str:
    where = f" ({signal.container})" if signal.container else ""
    return f"Signal: {signal.kind.replace('_', ' ')}{where}"


def _event_text(event: IncidentEvent, tool_calls: int) -> str | None:
    """One line for an event worth a place in the report, None for the rest."""
    payload = event.payload
    match event.kind:
        case "node_finished" if payload.get("node") == "investigate":
            return f"Investigated with {tool_calls} tool call{'' if tool_calls == 1 else 's'}"
        case "node_finished":
            return FINISHED.get(str(payload.get("node")))
        case "proposal" if payload.get("kind") == "act":
            return "Proposed: " + _steps_text(payload.get("steps") or [])
        case "proposal":
            return "Proposed no action"
        case "approval_requested":
            return "Asked for approval"
        case "reminder":
            return "Reminder sent"
        case "decision":
            return decision_text(
                str(payload.get("kind")),
                payload.get("via"),
                payload.get("text"),
                let_go=bool(payload.get("refused")),
            )
        case "action":  # Execute, one per step status change: {action, target, status}
            action, target = payload.get("action", "?"), payload.get("target", "?")
            return f"{action} {target}: {payload.get('status', '?')}"
        case "verification":
            verdict = "passed" if payload.get("ok") else "failed"
            return f"Check {payload.get('attempt', 1)} {verdict}"
        case "notice":
            return str(payload.get("text", "")) or None
    return None


def timeline(signals: list[Signal], events: list[IncidentEvent]) -> list[Entry]:
    """The incident's moments in time order: the signals that opened it, then its events.

    Node starts, tool calls and tool results are summed up into one line per investigation.
    """
    entries = [Entry(at=s.at, text=_clip(_signal_text(s), ENTRY_CHARS)) for s in signals]
    tool_calls = 0
    for event in events:
        if event.kind == "node_started" and event.payload.get("node") == "investigate":
            tool_calls = 0
        elif event.kind == "tool_call":
            tool_calls += 1
        text = _event_text(event, tool_calls)
        if text:
            entries.append(Entry(at=event.at, text=_clip(text, ENTRY_CHARS)))
    return sorted(entries, key=lambda entry: entry.at)  # stable: equal times keep their order


def action_taken(
    results: list[StepResult] | None, proposal: Proposal | None, host_id: str
) -> str | None:
    """What ran, one step after another; None when nothing ran.

    A `StepResult` names no host, so each step's host is read from the plan step at its
    `step_index`, falling back to the incident's host (a plan only ever acts on that host). A step
    that never started (one after a failed step) reads "skipped", and one the act tool refused at
    run time "refused".
    """
    if not results:
        return None
    steps = proposal.steps if proposal is not None else []

    def host(r: StepResult) -> str:
        return steps[r.step_index].host_id if 0 <= r.step_index < len(steps) else host_id

    def status(r: StepResult) -> str:
        if r.ok:
            return "done"
        if r.refused:
            return "refused"
        return "skipped" if r.started_at is None else "failed"

    return "; ".join(f"{r.action} {r.target} on {host(r)}: {status(r)}" for r in results)


def _raw_logs(state: IncidentState) -> list[str]:
    """Every piece of host text the run handed the model: Gather's logs and tool results."""
    context = state.get("context")
    logs = list(context.logs.values()) if context is not None else []
    logs += [m.text for m in state.get("messages", []) if isinstance(m, ToolMessage)]
    return logs


def planted_follow_up(state: IncidentState) -> tuple[list[PlantedPlace], str | None]:
    """Where host text tried to give orders (`graph/planted.py`) and the fixed sentence that
    names those places, or None. No text of the model's or the host's goes in, so the report and
    memory carry nothing new from the host."""
    places = planted_places(state.get("context"), state.get("messages", []), state.get("diagnosis"))
    return places, planted_sentence(places)


def _describe_diagnosis(diagnosis: Diagnosis | None) -> str:
    if diagnosis is None:
        return "(none)"
    lines = [
        f"Root cause: {diagnosis.root_cause}",
        f"Category: {diagnosis.category}, confidence {round(diagnosis.confidence * 100)}%"
        + (", unsure" if diagnosis.unsure else ""),
    ]
    lines += [f"- {e.source}: {e.detail}" for e in diagnosis.evidence]
    return "\n".join(lines)


def _describe_plan(proposal: Proposal | None) -> str:
    if proposal is None:
        return "(none)"
    steps = _steps_text([s.model_dump() for s in proposal.steps]) or "no action"
    lines = [f"Steps: {steps}", f"Reason: {proposal.reason}"]
    if proposal.human_instructions:
        lines.append(f"What the human was told to do: {proposal.human_instructions}")
    return "\n".join(lines)


async def _ask[T: BaseModel](deps: GraphDeps, incident_id: str, schema: type[T], prompt: str) -> T:
    await check_budget(deps.ledger, incident_id, deps.settings_store.budgets())
    return await invoke_structured(
        deps.models("main"),
        schema,
        [HumanMessage(prompt)],
        config=tracked(UsageTracker(incident_id, deps.ledger)),
        guard=deps.guard(),
    )


async def _write(deps: GraphDeps, incident_id: str, prompt: str) -> _Draft:
    """The model's draft; an empty one when the budget is spent or the model fails."""
    try:
        return await _ask(deps, incident_id, _Draft, prompt)
    except (BudgetExceeded, StructuredOutputFailed) as error:  # expected: the type says it all
        log.warning("report: writing from the record only (%s)", type(error).__name__)
    except Exception as error:  # the run must end with a report whatever the model does
        log.warning(
            "report: writing from the record only (%s)", type(error).__name__, exc_info=True
        )
    return _Draft()


async def _learn(deps: GraphDeps, state: IncidentState) -> None:
    """Keep what the human said is always true of the service as at most `MAX_FACTS` facts.

    Only a service incident with instructions asks the model. A failure keeps no facts and
    lets the run end: a fact helps later runs, this one is done without it.
    """
    subject, instructions = state["subject"], state.get("instructions") or []
    if subject.service_id is None or not instructions:
        return
    incident_id, context = state["incident_id"], state.get("context")
    proposal = state.get("proposal")
    steps = proposal.steps if proposal is not None else []
    prompt = prompts.load(
        "graph/report_facts",
        service=(context.service_name if context else None) or subject.service_id,
        instructions="\n".join(f"{n}. {text}" for n, text in enumerate(instructions, 1)),
        plan=_steps_text([s.model_dump() for s in steps]) or "no action",
    )
    try:
        found = await _ask(deps, incident_id, _Facts, prompt)
        texts = [_clip(text, FACT_CHARS) for text in found.facts]
        texts = list(dict.fromkeys(text for text in texts if text))[:MAX_FACTS]
        await deps.memory.replace_report_facts(subject.service_id, incident_id, texts)
    except Exception as error:
        log.warning("report: learning facts for %s failed: %s", incident_id, type(error).__name__)


async def _remember(
    deps: GraphDeps,
    incident: Incident,
    written: Report,
    decision: Decision | None,
    proposal: Proposal | None,
) -> None:
    try:
        await deps.memory.add_report(incident, written, decision, proposal=proposal)
    except Exception as error:  # memory helps later runs; this one is done without it
        log.warning("report: remembering %s failed: %s", incident.id, type(error).__name__)


async def _notify(deps: GraphDeps, incident: Incident, written: Report) -> None:
    try:
        await deps.notifier.report(incident, written)
    except Exception as error:  # a channel outage must not lose the report; the dashboard has it
        log.warning("report: notifying %s failed: %s", incident.id, type(error).__name__)


def make(deps: GraphDeps) -> Node:
    async def report(state: IncidentState) -> dict:
        incident_id = state["incident_id"]
        result = outcome(state)
        results = state.get("step_results")
        diagnosis, proposal = state.get("diagnosis"), state.get("proposal")
        said = outcome_text(result, results, proposal)
        fixed = said == OUTCOME_TEXT["resolved"]  # only then may the report say "fixed"
        incident = await deps.incidents.get(incident_id)
        title = incident.title if incident is not None else state["subject"].key
        decision: Decision | None = state.get("decision")
        let_go = bool(proposal and proposal.refused)
        human = (
            decision_text(decision.kind, decision.via, decision.text, let_go=let_go)
            if decision
            else None
        )
        taken = action_taken(results, proposal, state["subject"].host_id)
        entries = timeline(state.get("signals", []), await deps.bus.history(incident_id))

        prompt = prompts.load(
            "graph/report",
            title=title,
            outcome=said,
            diagnosis=_describe_diagnosis(diagnosis),
            plan=_describe_plan(proposal),
            decision=human or "(the human was not asked)",
            action=taken or "(nothing ran)",
            timeline="\n".join(f"- {e.at:%H:%M:%S} {e.text}" for e in entries) or "(empty)",
        )
        draft = await _write(deps, incident_id, prompt)

        def own(text: str, limit: int) -> str:
            text = _clip(text, limit)
            return text if fixed else _no_fixed_claim(text)

        follow_ups = [own(item, FOLLOW_UP_CHARS) for item in draft.follow_ups[:FOLLOW_UPS]]
        follow_ups = [item for item in follow_ups if item]
        if not follow_ups and proposal is not None and proposal.human_instructions:
            follow_ups = [own(proposal.human_instructions, FOLLOW_UP_CHARS)]
        root_cause = diagnosis.root_cause if diagnosis is not None else ""
        written = Report(
            title=title,
            summary=own(draft.summary, SUMMARY_CHARS) or f"{title}: {said[0].lower()}{said[1:]}.",
            cause=own(draft.cause, CAUSE_CHARS) or own(root_cause, CAUSE_CHARS) or NOT_DETERMINED,
            action_taken=taken,
            outcome=said,
            timeline=entries,
            follow_ups=[item for item in follow_ups if item],
            human_decision=human,
        )

        def record(kind: GuardKind, detail: str) -> None:
            if deps.guard_log is not None:
                deps.guard_log.record(incident_id, kind, detail)

        written = check_report(written, _raw_logs(state), record)
        places, planted = planted_follow_up(state)
        if planted is not None:  # after the check: Bleep's own text, never cut or removed
            follow_ups = [*written.follow_ups, planted]
            written = written.model_copy(update={"follow_ups": follow_ups, "planted_in": places})

        # Published, sent and remembered here; the runner closes the incident once this update
        # is checkpointed. A crash before that runs the node again (a second message; memory
        # replaces its record, it never doubles it) rather than closing an incident whose
        # report never reached the checkpoint, where the API reads it.
        await deps.bus.publish(incident_id, "report", written.model_dump(mode="json"))
        if incident is not None:
            await _notify(deps, incident, written)
            # Remembered with the facts the human taught; a re-run after a crash replaces the
            # record and those facts, it never doubles them. The runner closes the incident.
            await _remember(deps, incident, written, decision, proposal)
            await _learn(deps, state)
        return {"outcome": result, "report": written}

    return report
