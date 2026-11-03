"""Propose: a short plan of allowlisted actions, or "no action, informing you".

The model answers with a `Proposal`; code then checks it (`guard.plan_check`) before any human
sees it. A refused plan goes back to the model once with the reasons; refused again, it becomes
`no_action` with the reasons for the human. Every refusal is written to the guard log as
`blocked_action`. An unsure diagnosis is proposed on too, framed as "I am not sure, here is what I
found"; only a missing diagnosis or a spent budget skips the model. See docs/design/graph.md.
"""

import re
from typing import Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from bleep import prompts
from bleep.graph.deps import GraphDeps
from bleep.graph.state import IncidentState, Node
from bleep.guard.plan_check import Violation, check_plan, protected_names
from bleep.hosts.discovery import is_self
from bleep.llm.structured import StructuredOutputFailed, invoke_structured
from bleep.llm.usage import BudgetExceeded, UsageTracker, check_budget
from bleep.memory.text import describe_fact, describe_past
from bleep.schemas.graph import Diagnosis, GatherContext, PlanStep, Proposal
from bleep.schemas.incidents import Subject

NO_DIAGNOSIS = "Bleep has no diagnosis to act on, so it proposes no action."
NO_PLAN = "The model gave no usable plan, so Bleep proposes no action."
BUDGET = "The incident's token budget or the day's cost cap is spent, so Bleep proposes no action."
REFUSED = "Bleep's own checks refused the proposed plan twice, so nothing will run."


def make(deps: GraphDeps) -> Node:
    async def propose(state: IncidentState) -> dict:
        diagnosis = state.get("diagnosis")
        update: dict = {}
        if diagnosis is None:
            proposal = Proposal(kind="no_action", reason=NO_DIAGNOSIS)
        else:
            try:
                proposal = await _plan(deps, state, diagnosis)
            except BudgetExceeded:
                proposal = Proposal(kind="no_action", reason=BUDGET)
                update["outcome"] = "budget"
            except StructuredOutputFailed:
                proposal = Proposal(kind="no_action", reason=NO_PLAN)
        await deps.bus.publish(state["incident_id"], "proposal", proposal.model_dump(mode="json"))
        return update | {"proposal": proposal}

    return propose


def route(state: IncidentState) -> Literal["approval", "report"]:
    """A plan that acts needs a human's yes; "no action" goes straight to the report."""
    proposal = state.get("proposal")
    return "approval" if proposal is not None and proposal.kind == "act" else "report"


async def _plan(deps: GraphDeps, state: IncidentState, diagnosis: Diagnosis) -> Proposal:
    """Ask, check, ask once more with the reasons, then fall back to `no_action`."""
    incident_id, host_id = state["incident_id"], state["subject"].host_id
    enabled = deps.settings_store.enabled_actions()
    named = _incident_containers(deps, state)
    messages: list[BaseMessage] = [HumanMessage(_prompt(deps, state, diagnosis, enabled))]
    proposal = await _ask(deps, incident_id, messages)
    violations = check_plan(proposal, deps.registry, enabled, host_id, incident_containers=named)
    if not violations:
        return proposal
    _record(deps, incident_id, violations)
    messages += [
        AIMessage(proposal.model_dump_json()),
        HumanMessage(prompts.load("graph/propose_retry", violations=_bullets(violations))),
    ]
    retry = await _ask(deps, incident_id, messages)
    again = check_plan(retry, deps.registry, enabled, host_id, incident_containers=named)
    if not again:
        return retry
    _record(deps, incident_id, again)
    return _refused(retry, again)


def _incident_containers(deps: GraphDeps, state: IncidentState) -> set[str]:
    """The containers a plan may act on even while they work (#35): the ones the incident's
    signals name, and any the human names in an instruction. A name counts only as a whole word,
    so `shop-web` in an instruction does not name `shop-web-1`."""
    named = {s.container for s in state.get("signals", []) if s.container}
    said = " ".join(state.get("instructions", []))
    for container in deps.registry.snapshot(state["subject"].host_id) or []:
        if re.search(rf"(?<![\w.-]){re.escape(container.name)}(?![\w.-])", said):
            named.add(container.name)
    return named


async def _ask(deps: GraphDeps, incident_id: str, messages: list[BaseMessage]) -> Proposal:
    await check_budget(deps.ledger, incident_id, deps.settings_store.budgets())
    tracker = UsageTracker(incident_id, deps.ledger)
    return await invoke_structured(
        deps.models("main"), Proposal, messages, config={"callbacks": [tracker]}
    )


def _record(deps: GraphDeps, incident_id: str, violations: list[Violation]) -> None:
    if deps.guard_log is not None:
        for v in violations:
            deps.guard_log.record(incident_id, "blocked_action", v.text)


def _refused(proposal: Proposal, violations: list[Violation]) -> Proposal:
    """`no_action` that tells the human what was refused and why."""
    steps = [f"{n}. {_step(s)}" for n, s in enumerate(proposal.steps, start=1)]
    lines = [REFUSED, "Why:", _bullets(violations), "The refused plan:", *(steps or ["no steps"])]
    lines.append(f"The model's reason: {proposal.reason}")
    return Proposal(
        kind="no_action",
        reason=REFUSED,
        verification=proposal.verification,
        human_instructions="\n".join(lines),
    )


def _bullets(violations: list[Violation]) -> str:
    return "\n".join(f"- {v.text}" for v in violations)


def _step(step: PlanStep) -> str:
    params = ", ".join(f"{k}={v}" for k, v in sorted(step.params.items()))
    return f"{step.action} {step.target} on {step.host_id}" + (f" ({params})" if params else "")


# The prompt's sections are Bleep's own data, one fact per line, "none" when there is nothing.
# Memory is Bleep's own too; `describe_past` and `describe_fact` wrap any quote in it as untrusted.


def _prompt(deps: GraphDeps, state: IncidentState, diagnosis: Diagnosis, enabled: set[str]) -> str:
    subject = state["subject"]
    context = state.get("context")
    rules = [*context.host_rules, *context.service_rules] if context else []
    instructions = state.get("instructions", [])
    return prompts.load(
        "graph/propose",
        subject=_subject(subject, context),
        diagnosis=_diagnosis(diagnosis),
        containers=_containers(context),
        off_limits=_off_limits(deps, subject.host_id),
        rules="\n".join(f"- {r}" for r in rules) or "none",
        similar=_lines([describe_past(p) for p in context.similar] if context else []),
        facts=_lines([describe_fact(f) for f in context.facts] if context else []),
        urls="\n".join(f"- {u}" for u in _urls(deps, subject)) or "none",
        host_id=subject.host_id,
        actions=", ".join(f"`{a}`" for a in sorted(enabled)) or "none",
        instructions="\n".join(f"{n}. {t}" for n, t in enumerate(instructions, 1)) or "none",
        previous=_previous(state.get("proposal")),
        unsure=prompts.load("graph/propose_unsure") if diagnosis.unsure else "",
    )


def _lines(lines: list[str]) -> str:
    return "\n".join(f"- {line}" for line in lines) or "none"


def _subject(subject: Subject, context: GatherContext | None) -> str:
    if subject.kind == "host":
        return f"Host `{subject.host_id}` as a whole."
    name = context.service_name if context and context.service_name else subject.service_id
    return f"Service `{name}` on host `{subject.host_id}`."


def _diagnosis(d: Diagnosis) -> str:
    lines = [
        f"Root cause: {d.root_cause}",
        f"Category: {d.category}",
        f"Confidence: {d.confidence:.2f}",
    ]
    if d.affected_services:
        lines.append(f"Affected services: {', '.join(d.affected_services)}")
    lines += [f"- {e.source}: {e.detail}" for e in d.evidence]
    return "\n".join(lines)


def _containers(context: GatherContext | None) -> str:
    if context is None:
        return "none known"
    lines = []
    for c in context.containers:
        state = c.state + (f" (exit {c.exit_code})" if c.exit_code is not None else "")
        lines.append(
            f"- {c.name}: {state}, health {c.health}, restarts {c.restart_count}, image {c.image}"
        )
    lines += [f"- {m.name}: running, {m.mem_percent:.0f}% memory" for m in context.top_memory]
    return "\n".join(lines) or "none known"


def _off_limits(deps: GraphDeps, host_id: str) -> str:
    """Protected and Bleep's own containers, so the model need not learn them by refusal."""
    host = next((h for h in deps.registry.hosts() if h.config.id == host_id), None)
    protected = sorted(protected_names(host)) if host else []
    own = sorted(c.name for c in deps.registry.snapshot(host_id) or [] if is_self(c.labels))
    lines = [f"- {n} (protected)" for n in protected] + [f"- {n} (Bleep itself)" for n in own]
    return "\n".join(lines) or "none"


def _urls(deps: GraphDeps, subject: Subject) -> list[str]:
    service = deps.registry.service(subject.service_id) if subject.service_id else None
    return [u.url for u in service.urls] if service else []


def _previous(proposal: Proposal | None) -> str:
    if proposal is None:
        return "none"
    lines = [f"{proposal.kind}: {proposal.reason}"]
    lines += [f"{n}. {_step(s)}" for n, s in enumerate(proposal.steps, start=1)]
    if proposal.human_instructions:
        lines.append(f"Instructions you gave: {proposal.human_instructions}")
    return "\n".join(lines)
