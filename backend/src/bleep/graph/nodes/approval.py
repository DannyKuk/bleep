"""Approval: put the plan to the human and wait for a yes, a no or an instruction.

A plan the plan check refused twice (`Proposal.refused`) is put the same way while re-plans
remain, but only an instruction (back to Propose) or a no (on to the report) can answer it; a
yes is never recorded for it and never gets a grant (#113). A plan of no action whose fix the
enabled actions could run (`Proposal.fix_runnable`, #365) is put and answered the same way; both
are `Proposal.instruct_only`.

The node reads each target (a container's id, start time, restart count and state, or the
uuid a Coolify application name resolves to, so Execute can tell a stale plan), stores the plan
as a pending approval, publishes `approval_requested`, notifies, and calls `interrupt()` with
the approval's id. `IncidentRunner.resume` continues it once a channel has won
the approval, and only if the run is waiting on that same approval.

A node that interrupts runs again from its first line on resume, so it asks only when the
incident's latest approval is one the run has already consumed (`state["approval"]`); otherwise
it is this round's question, asked before the restart or the resume, and is not asked again. The
decision the store holds is the one that counts: a resume value that lost the race is ignored,
and one nobody recorded is recorded first. The grant comes from the store, which issues it only
for a plan it holds as approved.
"""

import logging
from typing import Literal

from langgraph.types import interrupt

from bleep.graph.deps import GraphDeps
from bleep.graph.replan import replan, replans_left
from bleep.graph.shown import shown_payload
from bleep.graph.state import IncidentState, Node
from bleep.graph.targets import read_app_uuid
from bleep.guard.grant import binds_app
from bleep.guard.redact import redacted_proposal
from bleep.hosts.engine import ContainerNotFound, EngineUnavailable
from bleep.schemas.graph import ApprovalRecord, Decision, PlanStep, Proposal, TargetSnapshot
from bleep.schemas.shown import Shown

log = logging.getLogger(__name__)

NOT_SENT = Shown.of("notice/not_sent")


class ApprovalNotDecided(RuntimeError):
    """The store refused a resumed decision but holds none for the approval: the run cannot
    tell what the human chose, so it stops rather than guess."""


def _why(error: Exception) -> Shown:
    """A short, fixed reason: engine messages are host text and do not belong in Bleep's own."""
    if isinstance(error, ContainerNotFound):
        return Shown.of("target/not_found")
    if isinstance(error, EngineUnavailable):
        return Shown.of("target/host_unreachable")
    return Shown.of("target/host_unknown")  # KeyError from the registry


def _unread(step: PlanStep, why: Shown) -> Shown:
    return Shown.of("target/unread", target=step.target, host=step.host_id, why=why)


async def _snapshots(
    deps: GraphDeps, proposal: Proposal
) -> tuple[list[TargetSnapshot], list[Shown]]:
    """Each target as it is now, and a line for each one that cannot be read.

    A Coolify step's target is bound to the application uuid it names now; every other step's
    target (Docker and `mcp.call` alike) to the container it names now, with its start time,
    restart count and state.
    """
    snapshots: list[TargetSnapshot] = []
    unread: list[Shown] = []
    seen: set[tuple[str, str, bool]] = set()
    for step in proposal.steps:
        key = (step.host_id, step.target, binds_app(step))
        if key in seen:
            continue
        seen.add(key)
        if binds_app(step):
            uuid, why = await read_app_uuid(deps.registry, step.host_id, step.target)
            if uuid is None:
                unread.append(_unread(step, why))
            else:
                snapshots.append(
                    TargetSnapshot(host_id=step.host_id, target=step.target, app_uuid=uuid)
                )
            continue
        try:
            summary = (await deps.registry.engine(step.host_id).inspect(step.target)).summary
        except (ContainerNotFound, EngineUnavailable, KeyError) as error:
            unread.append(_unread(step, _why(error)))
            continue
        snapshots.append(
            TargetSnapshot(
                host_id=step.host_id,
                target=step.target,
                container_id=summary.id,
                started_at=summary.started_at,
                restart_count=summary.restart_count,
                state=summary.state,
            )
        )
    return snapshots, unread


def make(deps: GraphDeps) -> Node:
    async def ask(state: IncidentState, proposal: Proposal) -> ApprovalRecord | list[Shown]:
        """Store, publish and send the question; the unread targets when one cannot be read."""
        incident_id = state["incident_id"]
        snapshots, unread = await _snapshots(deps, proposal)
        if unread:
            return unread
        request = await deps.approvals.request(incident_id, proposal, snapshots)
        await deps.bus.publish(
            incident_id,
            "approval_requested",
            {"approval_id": request.id, "plan_hash": request.plan_hash},
        )
        incident = await deps.incidents.get(incident_id)
        if incident is not None:
            try:
                await deps.notifier.proposal(incident, state.get("diagnosis"), proposal, request)
            except Exception as error:  # a channel outage must not strand the stored question
                log.warning("approval: notifying %s failed: %s", incident_id, type(error).__name__)
                await deps.bus.publish(incident_id, "notice", shown_payload(NOT_SENT))
        return await deps.approvals.current(incident_id)

    async def approval(state: IncidentState) -> dict:
        incident_id = state["incident_id"]
        proposal = state.get("proposal")
        if proposal is None or not (proposal.kind == "act" or proposal.instruct_only):
            return {"decision": None, "grant": None}  # nothing to approve: route goes to report
        if proposal.instruct_only and not replans_left(deps, state):
            # No instruction could follow. When the run already went back to Propose (any re-plan
            # counts: an instruction, a stale plan, an unreadable target) the budget is spent and
            # the run ends as `replan_limit`; with no trip so far it is the plain refusal.
            ended = await replan(deps, state) if state.get("replans", 0) else {}
            return {"decision": None, "grant": None, **ended}

        record = await deps.approvals.current(incident_id)
        consumed = state.get("approval")
        if record is None or (consumed is not None and record.id == consumed.id):
            asked = await ask(state, proposal)
            if isinstance(asked, list):
                targets = Shown.joined(asked, "; ")
                notice = Shown.of("notice/not_asked", targets=targets)
                return {"decision": None, "grant": None, **await replan(deps, state, notice)}
            record = asked

        decision = record.decision  # set when it was decided while the run was not waiting
        if decision is None:
            shown = redacted_proposal(proposal).model_dump(mode="json")
            question = {"approval_id": record.id, "proposal": shown}
            resumed = Decision.model_validate(interrupt(question))
            decision = await deps.approvals.resolve(incident_id, resumed, record.id)
            if decision is None:  # another channel won first; its decision stands
                latest = await deps.approvals.current(incident_id)
                if latest is None or latest.id != record.id or latest.decision is None:
                    raise ApprovalNotDecided(f"approval {record.id} of incident {incident_id}")
                decision = latest.decision

        await deps.bus.publish(
            incident_id,
            "decision",
            {"kind": decision.kind, "text": decision.text, "via": decision.via}
            # The key marks an answer to a plan of no action: a no to it reads "Let it go".
            | ({"refused": True} if proposal.instruct_only else {}),
        )
        approved = decision.kind == "approve" and proposal.kind == "act"
        update: dict = {
            "approval": record.request,
            "decision": decision,
            "grant": await deps.approvals.grant(incident_id) if approved else None,
        }
        if decision.kind == "instruct":
            if decision.text:
                update["instructions"] = [*state.get("instructions", []), decision.text]
            update |= await replan(deps, state)
        return update

    return approval


def route(state: IncidentState) -> Literal["propose", "execute", "report"]:
    """A yes goes to Execute; an instruction goes back to Propose, and so does a plan that could
    not be put to the human; a no closes with a report, and so does a run out of re-plans. A
    plan of no action put to the human (`instruct_only`) has only the instruction (back to
    Propose) or the report: never Execute."""
    proposal = state.get("proposal")
    if proposal is None or state.get("outcome") is not None:
        return "report"
    decision = state.get("decision")
    if proposal.instruct_only:
        return "propose" if decision is not None and decision.kind == "instruct" else "report"
    if proposal.kind != "act":
        return "report"
    if decision is None or decision.kind == "instruct":
        return "propose"
    return "execute" if decision.kind == "approve" else "report"
