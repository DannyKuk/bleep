"""Execute: run the approved plan only if the world still matches what was approved, and never
run a step twice.

Before the first step every target is read again and compared with the snapshot taken when the
human was asked: a target that is gone, recreated (another container id) or started again
(another start time with the same restart count) since then makes the plan stale, and so does an
approval that is not the one this run consumed. A start time moved by a rising restart count is a
restart policy repeating the failure the human approved a fix for, so it is not stale. Every step
then goes through its act tool's own checks (the grant against the stored plan, and the target
against the guard's rules). A stale or refused plan runs nothing: the
grant is voided, a `notice` says why, and the run goes back to Propose for a new plan and a new
question, at most `Budgets.replans` times (`graph/replan.py`).

The steps run in order. Each is written to the action log `started` before its act tool runs and
`done` or `failed` after it; a failed step stops the plan and the steps after it are `skipped`.
Every status change is published as an `action` event (`{action, target, status}`), and the
results are appended to `step_results`. Then Verify judges what happened.

A node rerun after a crash reads the log first. A finished step is never run again. A step left
`started` may have reached the engine, so it is never run again either: its target is read on the
host and the step counts as done when its effect shows (the container started, or stopped, later
than the start time in the approval's snapshot, both on the host's clock), else as failed. A step
not yet started is checked against its snapshot again before it runs, since the first check
happened before the crash.

The log's plan key is the plan hash followed by the approval's id: the same plan approved twice
(after Verify sent the run back) has the same hash and must still run the second time.
See docs/design/graph.md.
"""

import logging
from typing import Literal

from bleep.graph.deps import GraphDeps
from bleep.graph.replan import replan
from bleep.graph.state import IncidentState, Node
from bleep.guard.grant import GrantInvalid
from bleep.hosts.engine import ContainerNotFound, EngineUnavailable
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.graph import (
    REFUSED_DETAIL,
    ApprovalRecord,
    LoggedStep,
    PlanStep,
    StepResult,
    StepStatus,
    TargetSnapshot,
)
from bleep.tools.docker_act import StepRefused

log = logging.getLogger(__name__)

STALE = "The plan did not run, the state changed since approval: {changes}."
REFUSED = "The plan did not run, Bleep's checks refused it: {reasons}."
NOT_APPROVED = "The plan did not run, it is not the plan the human approved."
DONE_WORD = {"docker.restart": "restarted", "docker.stop": "stopped", "docker.start": "started"}


def _what(index: int, step: PlanStep) -> str:
    return f"step {index + 1} ({step.action} {step.target} on {step.host_id})"


def _snapshot(plan: ApprovalRecord, step: PlanStep) -> TargetSnapshot | None:
    return next(
        (s for s in plan.snapshots if (s.host_id, s.target) == (step.host_id, step.target)), None
    )


def _plan_key(plan: ApprovalRecord) -> str:
    return f"{plan.plan_hash}:{plan.id}"


async def _read(deps: GraphDeps, host_id: str, ref: str) -> tuple[ContainerSummary | None, str]:
    """The container as the host shows it now, or None and a fixed reason (never host text)."""
    try:
        return (await deps.registry.engine(host_id).inspect(ref)).summary, ""
    except ContainerNotFound:
        return None, "is gone"
    except EngineUnavailable:
        return None, "cannot be read, the host is unreachable"
    except KeyError:
        return None, "cannot be read, the host is not in the hosts file"


async def _changes(
    deps: GraphDeps, plan: ApprovalRecord, steps: list[PlanStep], touched: set[tuple[str, str]]
) -> list[str]:
    """One line per Docker target of `steps` that is not as it was approved.

    A target in `touched` was already acted on by an earlier step of this plan, so its start
    time is expected to have moved and only its id is compared.
    """
    changes: list[str] = []
    seen: set[tuple[str, str]] = set()
    for step in steps:
        key = (step.host_id, step.target)
        if not step.action.startswith("docker.") or key in seen:
            continue
        seen.add(key)
        where = f"{step.target} on {step.host_id}"
        snapshot = _snapshot(plan, step)
        if snapshot is None:
            changes.append(f"{where} was not read when the plan was approved")
            continue
        summary, why = await _read(deps, step.host_id, step.target)
        if summary is None:
            changes.append(f"{where} {why}")
        elif summary.id != snapshot.container_id:
            changes.append(f"{where} was recreated")
        elif key not in touched and _started_again(snapshot, summary):
            changes.append(f"{where} was started again")
    return changes


def _started_again(snapshot: TargetSnapshot, summary: ContainerSummary) -> bool:
    """Whether someone started the container since approval.

    A restart policy restarts a crash-looping container on its own and moves its start time
    every time; that raises the restart count, and the human approved a fix for exactly that
    failure, so it is not a change. A moved start time with the same count is a start by hand
    (or by another tool). Without a count (an approval stored before #87) any move counts.
    """
    if summary.started_at == snapshot.started_at:
        return False
    policy_restarted = (
        snapshot.restart_count is not None and summary.restart_count > snapshot.restart_count
    )
    return not policy_restarted


def _took_effect(step: PlanStep, summary: ContainerSummary, snapshot: TargetSnapshot) -> bool:
    """Whether the host shows `step`'s effect: a start or a finish later than the start time the
    snapshot holds.

    Both times are the host's own, so a host clock ahead of or behind Bleep's does not matter.
    The check before the step found the start time unchanged (or moved only by a restart
    policy), so a later start or finish is the step's doing; under a restart policy it may be
    the policy's, and Verify judges the service either way.
    """
    before = snapshot.started_at
    if step.action in ("docker.restart", "docker.start"):
        started = summary.started_at
        return (
            summary.state == "running"
            and started is not None
            and (before is None or started > before)
        )
    if step.action == "docker.stop":
        finished = summary.finished_at
        return (
            summary.state != "running"
            and finished is not None
            and (before is None or finished > before)
        )
    return False


def make(deps: GraphDeps) -> Node:
    async def publish(incident_id: str, step: PlanStep, status: StepStatus) -> None:
        payload = {"action": step.action, "target": step.target, "status": status}
        await deps.bus.publish(incident_id, "action", payload)

    def refusals(state: IncidentState, plan: ApprovalRecord) -> list[str]:
        """What the act tools' own checks refuse, for every step, before any step runs."""
        reasons: list[str] = []
        for index, step in enumerate(plan.proposal.steps):
            tool = deps.act_tools.get(step.action)
            if tool is None:
                reasons.append(f"{_what(index, step)}: Bleep cannot run this action yet")
                continue
            try:
                tool.check(step, state.get("grant"), deps.registry, plan=plan, step_index=index)
            except GrantInvalid as refusal:
                reasons.append(f"{_what(index, step)}: the approval does not cover it ({refusal})")
            except StepRefused as refusal:  # its text names the step already
                reasons.append(str(refusal))
        return reasons

    async def run_step(
        state: IncidentState, plan: ApprovalRecord, index: int, step: PlanStep
    ) -> StepResult:
        incident_id = state["incident_id"]
        started = await deps.action_log.start(incident_id, _plan_key(plan), index, step)
        if started is None:  # an earlier attempt wrote the step after all: never run it again
            row = next(
                s
                for s in await deps.action_log.steps(incident_id)
                if s.plan_key == _plan_key(plan) and s.result.step_index == index
            )
            return row.result
        await publish(incident_id, step, "started")
        tool = deps.act_tools.get(step.action)
        # Any other exception leaves the step `started`: a rerun reads the host, never re-runs it.
        try:
            if tool is None:
                raise StepRefused(f"{_what(index, step)}: Bleep cannot run this action yet")
            ran = await tool.run(
                step, state.get("grant"), deps.registry, plan=plan, step_index=index
            )
            # The logged start, as the action log keeps it and a rerun reads it back.
            result = ran.model_copy(update={"started_at": started})
        except (GrantInvalid, StepRefused) as refusal:
            # Nothing reached the engine: no start time in the result (the log row keeps its).
            result = StepResult(
                step_index=index,
                action=step.action,
                target=step.target,
                ok=False,
                detail=f"{REFUSED_DETAIL}: {refusal}",
                finished_at=deps.clock(),
            )
        await deps.action_log.finish(incident_id, _plan_key(plan), result)
        await publish(incident_id, step, "done" if result.ok else "failed")
        return result

    async def confirm(
        state: IncidentState, plan: ApprovalRecord, index: int, step: PlanStep, row: LoggedStep
    ) -> StepResult:
        """A step left `started` by a crash: read its target, never run it again."""
        incident_id = state["incident_id"]
        since = row.result.started_at
        snapshot = _snapshot(plan, step)
        summary, why = await _read(
            deps, step.host_id, snapshot.container_id if snapshot else step.target
        )
        if summary is not None and snapshot is not None and _took_effect(step, summary, snapshot):
            word = DONE_WORD.get(step.action, "ran")
            ok, detail = True, f"{word} {step.target}, confirmed after a restart"
        else:
            reason = f"the container {why}" if summary is None else "its effect does not show"
            ok, detail = False, f"not confirmed after a restart ({reason}); not run again"
        result = StepResult(
            step_index=index,
            action=step.action,
            target=step.target,
            ok=ok,
            detail=detail,
            started_at=since,
            finished_at=deps.clock(),
        )
        await deps.action_log.finish(incident_id, _plan_key(plan), result)
        await publish(incident_id, step, "done" if ok else "failed")
        return result

    async def skip(
        state: IncidentState, plan: ApprovalRecord, index: int, step: PlanStep, why: str
    ) -> StepResult:
        detail = f"skipped: {why}"
        await deps.action_log.skip(state["incident_id"], _plan_key(plan), index, step, detail)
        await publish(state["incident_id"], step, "skipped")
        return StepResult(
            step_index=index, action=step.action, target=step.target, ok=False, detail=detail
        )

    async def back_to_propose(state: IncidentState, notice: str) -> dict:
        """Void the grant and plan again. The decision goes too, since the yes was for this plan
        and a next "no action" must not report it; a run out of re-plans keeps it, so its
        report says the human approved and Bleep stopped."""
        log.info("execute: %s back to propose: %s", state["incident_id"], notice)
        update = await replan(deps, state, notice)
        if update.get("outcome") is None:
            update["decision"] = None
        return {"grant": None, **update}

    async def execute(state: IncidentState) -> dict:
        incident_id = state["incident_id"]
        proposal, consumed = state.get("proposal"), state.get("approval")
        plan = await deps.approvals.current(incident_id)
        if (
            plan is None
            or consumed is None
            or plan.id != consumed.id
            or plan.decision is None
            or plan.decision.kind != "approve"
            or plan.proposal != proposal
        ):
            return await back_to_propose(state, NOT_APPROVED)
        steps = plan.proposal.steps

        logged = {
            s.result.step_index: s
            for s in await deps.action_log.steps(incident_id)
            if s.plan_key == _plan_key(plan)
        }
        if not logged:  # nothing of this plan has run: check everything before the first step
            changes = await _changes(deps, plan, steps, set())
            if changes:
                return await back_to_propose(state, STALE.format(changes="; ".join(changes)))
            reasons = refusals(state, plan)
            if reasons:
                return await back_to_propose(state, REFUSED.format(reasons="; ".join(reasons)))

        results: list[StepResult] = []
        stop: str | None = None  # why the remaining steps are skipped
        for index, step in enumerate(steps):
            row = logged.get(index)
            if row is not None and row.status == "started":
                result = await confirm(state, plan, index, step, row)
            elif row is not None:
                result = row.result  # finished before a restart: never again
            elif stop is not None:
                result = await skip(state, plan, index, step, stop)
            else:
                if logged:  # a rerun: the first check happened before the crash
                    touched = {(s.host_id, s.target) for s in steps[:index]}
                    changes = await _changes(deps, plan, [step], touched)
                    if changes:
                        stop = "the state changed since approval: " + "; ".join(changes)
                        results.append(await skip(state, plan, index, step, stop))
                        continue
                result = await run_step(state, plan, index, step)
            results.append(result)
            if not result.ok and stop is None:
                stop = f"step {index + 1} did not succeed"
        return {
            "executed": plan.id,
            "step_results": [*(state.get("step_results") or []), *results],
        }

    return execute


def route(state: IncidentState) -> Literal["propose", "verify", "report"]:
    """A plan that ran goes to Verify; a stale or refused one back to Propose for a new
    question, unless the run is out of re-plans."""
    if state.get("outcome") is not None:
        return "report"
    approval = state.get("approval")
    ran = approval is not None and state.get("executed") == approval.id
    return "verify" if ran else "propose"
