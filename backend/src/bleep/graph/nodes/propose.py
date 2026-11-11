"""Propose: a short plan of allowlisted actions, or "no action, informing you".

The model answers with a `Proposal`; code then checks it (`guard.plan_check`) before any human
sees it. A refused plan goes back to the model once with the reasons; refused again, it becomes
`no_action` with the reasons for the human. Every refusal is written to the guard log as
`blocked_action`. An unsure diagnosis is proposed on too, framed as "I am not sure, here is what I
found"; only a missing diagnosis or a spent budget skips the model. See docs/design/graph.md.

The texts Bleep writes in place of the model's (a fallback reason, a refusal's reasons and what
the human is told) are stored in English, which is what a later Propose and the report read, and
kept as the templates they are made of (`prompts/shown/proposal/`), to show them in the person's
language (#455); a test holds both to the same words.
"""

import logging
import re
from typing import Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from bleep import prompts
from bleep.graph.deps import GraphDeps
from bleep.graph.host_text import service_name
from bleep.graph.language import language_rule
from bleep.graph.replan import replans_left
from bleep.graph.state import IncidentState, Node
from bleep.guard.grant import binds_app
from bleep.guard.plan_check import (
    StepRule,
    Violation,
    check_plan,
    coolify_members,
    host_urls,
    protected_names,
    step_violation,
)
from bleep.guard.redact import redacted_proposal, redacted_step
from bleep.hosts.coolify_names import app_protected_by
from bleep.hosts.discovery import is_self
from bleep.hosts.registry import MIN_ID_PREFIX, Registry
from bleep.llm.structured import StructuredOutputFailed, invoke_structured
from bleep.llm.usage import BudgetExceeded, UsageTracker, check_budget, tracked
from bleep.memory.text import describe_fact, describe_past
from bleep.schemas.graph import (
    Diagnosis,
    GatherContext,
    PlanStep,
    Proposal,
    ProposalDraft,
    VerificationPlan,
)
from bleep.schemas.incidents import Subject
from bleep.schemas.shown import Shown
from bleep.tools.registry import ActCatalog, PlanRules

log = logging.getLogger(__name__)

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
BY_HAND_ONLY = (
    "A container that works is acted on only when it is this incident's own. If it is the one at "
    "fault, act on it by hand."
)
STOP_BY_HAND = (
    "A container is stopped only when this incident's signals name it or you name it in an "
    "instruction. If it should stop, name it, or stop it by hand."
)
STOP_BY_HAND_ONLY = (
    "A container is stopped only when this incident's signals name it. If it should stop, stop it "
    "by hand."
)

UNCLEAR_BY_HAND = (
    "Bleep never acts on a container whose name is also the start of another container's id, "
    "and an instruction cannot change that. Act on it by hand, or rename it (`docker rename`) so "
    "its name reads as no container's id."
)
HIDDEN_BY_HAND = (
    "A step runs only when you can read every param of it, and Bleep hides anything that looks "
    "like a secret. If the change is needed, make it by hand."
)
# Each text above as its template.
SHOWN = {
    NO_DIAGNOSIS: Shown.of("proposal/no_diagnosis"),
    NO_PLAN: Shown.of("proposal/no_plan"),
    BUDGET: Shown.of("proposal/budget"),
    REFUSED: Shown.of("proposal/refused"),
    INSTRUCT: Shown.of("proposal/instruct"),
    BY_HAND: Shown.of("proposal/by_hand"),
    BY_HAND_ONLY: Shown.of("proposal/by_hand_only"),
    STOP_BY_HAND: Shown.of("proposal/stop_by_hand"),
    STOP_BY_HAND_ONLY: Shown.of("proposal/stop_by_hand_only"),
    UNCLEAR_BY_HAND: Shown.of("proposal/unclear_by_hand"),
    HIDDEN_BY_HAND: Shown.of("proposal/hidden_by_hand"),
}


def make(deps: GraphDeps) -> Node:
    async def propose(state: IncidentState) -> dict:
        diagnosis = state.get("diagnosis")
        update: dict = {}
        if diagnosis is None:
            proposal = _no_action(NO_DIAGNOSIS)
        else:
            try:
                proposal = await _plan(deps, state, diagnosis)
            except BudgetExceeded:
                proposal = _no_action(BUDGET)
                update["outcome"] = "budget"
            except StructuredOutputFailed:
                proposal = _no_action(NO_PLAN)
        shown = redacted_proposal(proposal).model_dump(mode="json")
        await deps.bus.publish(state["incident_id"], "proposal", shown)
        return update | {"proposal": proposal}

    return propose


def _no_action(reason: str) -> Proposal:
    """No action, for `reason`, one of Bleep's own."""
    return Proposal(kind="no_action", reason=reason, shown={"reason": SHOWN[reason]})


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
    named = _named_containers(deps, state)
    incident = named | _service_containers(deps, state)
    rules = _step_rules(deps)

    def check(plan: Proposal) -> list[Violation]:
        found = check_plan(
            plan,
            deps.registry,
            enabled,
            host_id,
            incident_containers=incident,
            named_containers=named,
            step_rules=rules,
        )
        refused = {v.step for v in found}
        return found + [v for v in _unclear_names(plan, deps.registry) if v.step not in refused]

    mcp = await _mcp_section(deps, incident_id, enabled)
    messages: list[BaseMessage] = [HumanMessage(_prompt(deps, state, diagnosis, enabled, mcp))]
    proposal = _read(await _ask(deps, incident_id, messages), deps.registry, host_id)
    violations = check(proposal)
    if not violations:
        return proposal
    _record(deps, incident_id, violations)
    messages += [
        AIMessage(proposal.model_dump_json(include=set(ProposalDraft.model_fields))),
        HumanMessage(prompts.load("graph/propose_retry", violations=_bullets(violations))),
    ]
    retry = _read(await _ask(deps, incident_id, messages), deps.registry, host_id)
    again = check(retry)
    if not again:
        return retry
    _record(deps, incident_id, again)
    unclear = any(v in again for v in _unclear_names(retry, deps.registry))
    return _refused(retry, again, can_instruct=replans_left(deps, state), unclear=unclear)


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


def _read(proposal: Proposal, registry: Registry, host_id: str) -> Proposal:
    """The model's plan as Bleep checks it and the human approves it."""
    read = _without_replaced(_by_name(proposal, registry), registry)
    return _known_checks(read, registry, host_id) if read.kind == "no_action" else read


def _known_checks(proposal: Proposal, registry: Registry, host_id: str) -> Proposal:
    """A plan of no action without the checks a plan that acts could not name: a URL that is
    not one of the host's health checks, a container not on the host. Nothing runs such a
    plan's checks but a look's, which reads only its own service (`verify.check_look`, #421);
    they are dropped in code rather than sent back to the model, so a retry that fails cannot
    cost the plan its advice."""
    planned = proposal.verification
    configured = host_urls(registry, host_id)
    kept = VerificationPlan(
        urls=[url for url in planned.urls if url in configured],
        containers=[c for c in planned.containers if registry.container(host_id, c) is not None],
    )
    if kept == planned:
        return proposal
    log.info("propose: dropped the unknown checks of a plan of no action on %s", host_id)
    return proposal.model_copy(update={"verification": kept})


def _by_name(proposal: Proposal, registry: Registry) -> Proposal:
    """The plan with every container target given by its id or an id prefix written as the
    container's name, the one the registry resolves it to, so the plan check, the snapshot, the
    grant and both displays all read the name the human approves (#321). A target the registry
    cannot resolve stays as written, for the plan check to refuse.

    The registry takes an exact id or name in list order, Docker a full id before a name; they
    differ only for a name that is also another container's id, which `_unclear_names` refuses."""
    steps = []
    for step in proposal.steps:
        container = None if binds_app(step) else registry.container(step.host_id, step.target)
        named = container is None or container.name == step.target
        steps.append(step if named else step.model_copy(update={"target": container.name}))
    return proposal.model_copy(update={"steps": steps})


def _unclear_names(proposal: Proposal, registry: Registry) -> list[Violation]:
    """A container target that is one container's name and the start of another's id, in any
    case (Docker writes ids in lower case). Docker acts on the named one, but the human reading
    the name could take it for the other's id (#321)."""
    found = []
    for number, step in enumerate(proposal.steps, start=1):
        if binds_app(step) or len(step.target) < MIN_ID_PREFIX:
            continue
        containers = registry.snapshot(step.host_id) or []
        if not any(c.name == step.target for c in containers):
            continue
        as_id = step.target.lower()
        if any(c.name != step.target and c.id.startswith(as_id) for c in containers):
            why = (
                f"{step.target} is a container's name and also the start of another container's"
                " id, so the human could not tell which one it acts on; leave it out, and if the"
                " fix needs it, answer no_action and say in human_instructions what to do"
            )
            shown = Shown.of("refusal/unclear_name", target=step.target)
            found.append(step_violation("not_found", number, step, why, shown))
    return found


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


def _named_containers(deps: GraphDeps, state: IncidentState) -> set[str]:
    """The containers the incident names, the only ones a plan may stop (#276) and, with
    `_service_containers`, the ones it may act on even while they work (#35, #112). They are read
    only from trusted sources, never from the diagnosis, a plan, a tool result, the logs or
    memory:

    - the ones the incident's signals name (engine-reported names);
    - for a host incident about memory, the top memory users Gather measured;
    - any the human names in an instruction. Approval fills the instructions only from the
      human's own `instruct` decision. A name counts only as a whole word, in any case, so
      `shop-web` does not name `shop-web-1` and `shop-web-1.local` names neither; a period or
      comma after it ends it ("Restart shop-web-1."). Any mention counts, even "leave shop-db-1
      alone": the human still approves the plan."""
    subject, signals = state["subject"], state.get("signals", [])
    named = {s.container for s in signals if s.container}
    context = state.get("context")
    if subject.kind == "host" and context and any(s.kind == "memory" for s in signals):
        named |= {m.name for m in context.top_memory}
    said = " ".join(state.get("instructions", []))
    for container in deps.registry.snapshot(subject.host_id) or []:
        whole = rf"(?<![\w.-]){re.escape(container.name)}(?![\w-])(?!\.\w)"
        if re.search(whole, said, re.IGNORECASE):
            named.add(container.name)
    return named


def _service_containers(deps: GraphDeps, state: IncidentState) -> set[str]:
    """For a service incident no signal names a container of (its URL checks failed and nothing
    else), the service's own containers, from the registry. The URL is the only symptom and any
    of them may be the one at fault, so a plan may act on one even while it works, but not stop
    it (#112, #276)."""
    subject, signals = state["subject"], state.get("signals", [])
    if subject.kind != "service" or not signals or any(s.container for s in signals):
        return set()
    service = deps.registry.service(subject.service_id or "")
    return {c.name for c in service.containers} if service else set()


async def _ask(deps: GraphDeps, incident_id: str, messages: list[BaseMessage]) -> Proposal:
    await check_budget(deps.ledger, incident_id, deps.settings_store.budgets())
    tracker = UsageTracker(incident_id, deps.ledger)
    draft = await invoke_structured(
        deps.models("main"), ProposalDraft, messages, config=tracked(tracker), guard=deps.guard()
    )
    return Proposal.from_draft(draft)


def _record(deps: GraphDeps, incident_id: str, violations: list[Violation]) -> None:
    if deps.guard_log is not None:
        for v in violations:
            deps.guard_log.record(incident_id, "blocked_action", v.text)


def _refused(
    proposal: Proposal, violations: list[Violation], *, can_instruct: bool, unclear: bool = False
) -> Proposal:
    """`no_action` that tells the human what was refused and why, and what they can do next."""
    shown_steps = [redacted_step(s) for s in proposal.steps]
    steps = [f"{n}. {_step(s)}" for n, s in enumerate(shown_steps, start=1)]
    lines = [REFUSED, "Why:", _bullets(violations), "The refused plan:", *(steps or ["no steps"])]
    lines.append(f"The model's reason: {proposal.reason}")
    advice = _advice(violations, can_instruct=can_instruct, unclear=unclear)
    lines += advice
    reasons = [_shown_reason(v) for v in violations]
    told = [
        SHOWN[REFUSED],
        Shown.of("proposal/why"),
        *(Shown.of("proposal/reason_line", reason=r) for r in reasons),
        Shown.of("proposal/refused_plan"),
        *([_step_shown(n, s) for n, s in enumerate(shown_steps, start=1)] or [_NO_STEPS]),
        Shown.of("proposal/model_reason", reason=proposal.reason),
        *(SHOWN[line] for line in advice),
    ]
    return Proposal(
        kind="no_action",
        refused=True,
        refused_steps=shown_steps,
        refusal_reasons=[v.text for v in violations],
        reason=REFUSED,
        verification=proposal.verification,
        human_instructions="\n".join(lines),
        shown={"reason": SHOWN[REFUSED], "human_instructions": Shown.joined(told, "\n")},
        refusal_reasons_shown=reasons,
    )


_NO_STEPS = Shown.of("proposal/no_steps")


def _advice(violations: list[Violation], *, can_instruct: bool, unclear: bool) -> list[str]:
    """What the human can do about a refusal, one line each."""
    rules = {v.rule for v in violations}
    lines = []
    if "healthy_target" in rules:
        lines.append(BY_HAND if can_instruct else BY_HAND_ONLY)
    if "unnamed_stop" in rules:
        lines.append(STOP_BY_HAND if can_instruct else STOP_BY_HAND_ONLY)
    if "hidden_param" in rules:
        lines.append(HIDDEN_BY_HAND)
    if unclear:
        lines.append(UNCLEAR_BY_HAND)
    if can_instruct:
        lines.append(INSTRUCT)
    return lines


def _shown_reason(violation: Violation) -> Shown:
    """The violation as a shown text; one with none (a third party's rule) shows as written."""
    return violation.shown or Shown.of("refusal/text", text=violation.text)


def _step_shown(number: int, step: PlanStep) -> Shown:
    """`_step` as a shown text, numbered."""
    values = {"number": str(number), "action": step.action, "target": step.target}
    params = ", ".join(f"{k}={v}" for k, v in sorted(step.params.items()))
    if params:
        return Shown.of("proposal/refused_step_params", **values, host=step.host_id, params=params)
    return Shown.of("proposal/refused_step", **values, host=step.host_id)


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
        language=language_rule("graph/propose", deps.settings_store.language()),
    )


def _lines(lines: list[str]) -> str:
    return "\n".join(f"- {line}" for line in lines) or "none"


def _subject(subject: Subject, context: GatherContext | None) -> str:
    if subject.kind == "host":
        return f"Host `{subject.host_id}` as a whole."
    name = service_name(subject, context)
    shown = name if name.startswith("<untrusted ") else f"`{name}`"
    return f"Service {shown} on host `{subject.host_id}`."


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
    """Protected and Bleep's own containers, so the model need not learn them by refusal: each
    protected entry, and each container a protected Coolify application entry covers (#319)."""
    host = next((h for h in deps.registry.hosts() if h.config.id == host_id), None)
    protected = sorted(protected_names(host)) if host else []
    containers = sorted(deps.registry.snapshot(host_id) or [], key=lambda c: c.name)
    apps = [
        (c.name, app)
        for c in containers
        if c.name not in protected
        and not is_self(c.labels)
        and (app := app_protected_by(c, protected))
    ]
    own = [c.name for c in containers if is_self(c.labels)]
    lines = [f"- {n} (protected)" for n in protected]
    lines += [f"- {n} (protected: Coolify application {app})" for n, app in apps]
    lines += [f"- {n} (Bleep itself)" for n in own]
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
