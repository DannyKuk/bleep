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
from bleep.graph.replan import replans_left
from bleep.graph.state import IncidentState, Node
from bleep.guard.plan_check import (
    StepRule,
    Violation,
    check_plan,
    coolify_members,
    protected_names,
)
from bleep.guard.redact import redacted_proposal, redacted_step
from bleep.hosts.discovery import is_self
from bleep.hosts.registry import Registry
from bleep.llm.structured import StructuredOutputFailed, invoke_structured
from bleep.llm.usage import BudgetExceeded, UsageTracker, check_budget, tracked
from bleep.memory.text import describe_fact, describe_past
from bleep.schemas.graph import Diagnosis, GatherContext, PlanStep, Proposal
from bleep.schemas.incidents import Subject
from bleep.tools.registry import ActCatalog, PlanRules

NO_DIAGNOSIS = "Bleep has no diagnosis to act on, so it proposes no action."
NO_PLAN = "The model gave no usable plan, so Bleep proposes no action."
BUDGET = "The incident's token budget or the day's cost cap is spent, so Bleep proposes no action."
REFUSED = "Bleep's own checks refused the proposed plan twice, so nothing will run."
MCP_CALL = "mcp.call"
INSTRUCT = (
    "You can tell Bleep what to do instead and it will plan again with that; it can also let "
    "this go. Nothing can be approved from a refused plan."
)
BY_HAND = (
    "A container that works is acted on only when it is this incident's own or when you name it "
    "in an instruction. If it is the one at fault, name it, or act on it by hand."
)


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
        shown = redacted_proposal(proposal).model_dump(mode="json")
        await deps.bus.publish(state["incident_id"], "proposal", shown)
        return update | {"proposal": proposal}

    return propose


def route(state: IncidentState) -> Literal["approval", "report"]:
    """A plan that acts needs a human's yes, and a refused plan the human's instruction or
    dismissal (Approval sends it on to the report itself once no re-plan is left); any other
    "no action" goes straight to the report."""
    proposal = state.get("proposal")
    if proposal is None:
        return "report"
    return "approval" if proposal.kind == "act" or proposal.refused else "report"


async def _plan(deps: GraphDeps, state: IncidentState, diagnosis: Diagnosis) -> Proposal:
    """Ask, check, ask once more with the reasons, then fall back to `no_action`."""
    incident_id, host_id = state["incident_id"], state["subject"].host_id
    enabled = deps.settings_store.enabled_actions()
    named = _incident_containers(deps, state)
    rules = _step_rules(deps)

    def check(plan: Proposal) -> list[Violation]:
        return check_plan(
            plan, deps.registry, enabled, host_id, incident_containers=named, step_rules=rules
        )

    mcp = await _mcp_section(deps, incident_id, enabled)
    messages: list[BaseMessage] = [HumanMessage(_prompt(deps, state, diagnosis, enabled, mcp))]
    proposal = _without_replaced(await _ask(deps, incident_id, messages), deps.registry)
    violations = check(proposal)
    if not violations:
        return proposal
    _record(deps, incident_id, violations)
    messages += [
        AIMessage(proposal.model_dump_json()),
        HumanMessage(prompts.load("graph/propose_retry", violations=_bullets(violations))),
    ]
    retry = _without_replaced(await _ask(deps, incident_id, messages), deps.registry)
    again = check(retry)
    if not again:
        return retry
    _record(deps, incident_id, again)
    return _refused(retry, again, can_instruct=replans_left(deps, state))


def _step_rules(deps: GraphDeps) -> dict[str, StepRule]:
    """The rules the act handlers keep for their own actions' steps (`PlanRules`)."""
    return {
        action: handler.plan_violations
        for action, handler in deps.act_tools.items()
        if isinstance(handler, PlanRules)
    }


async def _mcp_section(deps: GraphDeps, incident_id: str, enabled: set[str]) -> str:
    """How to write an `mcp.call` step, with the act tools and what their server says about
    them; empty unless the action is enabled and its act handler can name them, so the prompt
    is exactly as before while the MCP plugin is off or has no act tools."""
    handler = deps.act_tools.get(MCP_CALL)
    if MCP_CALL not in enabled or not isinstance(handler, ActCatalog):
        return ""
    return prompts.load(
        "graph/propose_mcp",
        tools=", ".join(f"`{name}`" for name in handler.act_tool_names()),
        schemas=await handler.act_tool_schemas(incident_id),
    )


def _without_replaced(proposal: Proposal, registry: Registry) -> Proposal:
    """The plan without verification checks of containers a redeploy replaces: Coolify names
    the new ones afresh, so the old names would fail every check. Verify checks a redeployed
    application's new containers itself."""
    replaced = {
        c.name
        for s in proposal.steps
        if s.action == "coolify.redeploy"
        for c in coolify_members(registry.snapshot(s.host_id) or [], s.target)
    }
    planned = proposal.verification.containers
    if not replaced.intersection(planned):
        return proposal
    kept = [name for name in planned if name not in replaced]
    verification = proposal.verification.model_copy(update={"containers": kept})
    return proposal.model_copy(update={"verification": verification})


def _incident_containers(deps: GraphDeps, state: IncidentState) -> set[str]:
    """The containers a plan may act on even while they work (#35, #112), read only from trusted
    sources, never from the diagnosis, a plan, a tool result, the logs or memory:

    - the ones the incident's signals name (engine-reported names);
    - for a service incident no signal names a container of (its URL checks failed and nothing
      else), the service's own containers, from the registry: the URL is the only symptom, so
      any of them may be the one at fault;
    - for a host incident about memory, the top memory users Gather measured;
    - any the human names in an instruction. Approval fills the instructions only from the
      human's own `instruct` decision. A name counts only as a whole word, in any case, so
      `shop-web` does not name `shop-web-1` and `shop-web-1.local` names neither; a period or
      comma after it ends it ("Restart shop-web-1."). Any mention counts, even "leave shop-db-1
      alone": the human still approves the plan."""
    subject, signals = state["subject"], state.get("signals", [])
    named = {s.container for s in signals if s.container}
    if subject.kind == "service" and signals and not named:
        service = deps.registry.service(subject.service_id or "")
        named |= {c.name for c in service.containers} if service else set()
    context = state.get("context")
    if subject.kind == "host" and context and any(s.kind == "memory" for s in signals):
        named |= {m.name for m in context.top_memory}
    said = " ".join(state.get("instructions", []))
    for container in deps.registry.snapshot(subject.host_id) or []:
        whole = rf"(?<![\w.-]){re.escape(container.name)}(?![\w-])(?!\.\w)"
        if re.search(whole, said, re.IGNORECASE):
            named.add(container.name)
    return named


async def _ask(deps: GraphDeps, incident_id: str, messages: list[BaseMessage]) -> Proposal:
    await check_budget(deps.ledger, incident_id, deps.settings_store.budgets())
    tracker = UsageTracker(incident_id, deps.ledger)
    return await invoke_structured(
        deps.models("main"), Proposal, messages, config=tracked(tracker), guard=deps.guard()
    )


def _record(deps: GraphDeps, incident_id: str, violations: list[Violation]) -> None:
    if deps.guard_log is not None:
        for v in violations:
            deps.guard_log.record(incident_id, "blocked_action", v.text)


def _refused(proposal: Proposal, violations: list[Violation], *, can_instruct: bool) -> Proposal:
    """`no_action` that tells the human what was refused and why, and what they can do next."""
    steps = [f"{n}. {_step(redacted_step(s))}" for n, s in enumerate(proposal.steps, start=1)]
    lines = [REFUSED, "Why:", _bullets(violations), "The refused plan:", *(steps or ["no steps"])]
    lines.append(f"The model's reason: {proposal.reason}")
    if any(v.rule == "healthy_target" for v in violations):
        lines.append(BY_HAND)
    if can_instruct:
        lines.append(INSTRUCT)
    return Proposal(
        kind="no_action",
        refused=True,
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


def _prompt(
    deps: GraphDeps, state: IncidentState, diagnosis: Diagnosis, enabled: set[str], mcp: str
) -> str:
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
        mcp=mcp,
        instructions="\n".join(f"{n}. {t}" for n, t in enumerate(instructions, 1)) or "none",
        previous=_previous(state.get("proposal")),
        unsure=prompts.load("graph/propose_unsure") if diagnosis.unsure else "",
        untrusted_rule=prompts.load("untrusted/propose").strip(),
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
