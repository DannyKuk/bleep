"""Approval: put the plan to the human and wait for a yes, a no or an instruction.

The node reads each target container (its id and start time, so Execute can tell a stale plan),
stores the plan as a pending approval, publishes `approval_requested`, notifies, and calls
`interrupt()`. `IncidentRunner.resume` continues it once a channel has won the approval.

A node that interrupts runs again from its first line on resume, so it asks only when the
incident's latest approval is one the run has already consumed (`state["approval"]`); otherwise
it is this round's question, asked before the restart or the resume, and is not asked again. The
decision the store holds is the one that counts: a resume value that lost the race is ignored,
and one nobody recorded is recorded first. The grant comes from the store, which issues it only
for a plan it holds as approved.
"""

from typing import Literal

from langgraph.types import interrupt

from bleep.graph.deps import GraphDeps
from bleep.graph.state import IncidentState, Node
from bleep.hosts.engine import ContainerNotFound, EngineUnavailable
from bleep.schemas.graph import ApprovalRecord, Decision, Proposal, TargetSnapshot


def _why(error: Exception) -> str:
    """A short, fixed reason: engine messages are host text and do not belong in Bleep's own."""
    if isinstance(error, ContainerNotFound):
        return "container not found"
    if isinstance(error, EngineUnavailable):
        return "host unreachable"
    return "host not in the hosts file"  # KeyError from the registry


async def _snapshots(deps: GraphDeps, proposal: Proposal) -> tuple[list[TargetSnapshot], list[str]]:
    """Each Docker target as it is now, and a line for each one that cannot be read."""
    snapshots: list[TargetSnapshot] = []
    unread: list[str] = []
    seen: set[tuple[str, str]] = set()
    for step in proposal.steps:
        key = (step.host_id, step.target)
        if not step.action.startswith("docker.") or key in seen:
            continue
        seen.add(key)
        try:
            summary = (await deps.registry.engine(step.host_id).inspect(step.target)).summary
        except (ContainerNotFound, EngineUnavailable, KeyError) as error:
            unread.append(f"{step.target} on {step.host_id}: {_why(error)}")
            continue
        snapshots.append(
            TargetSnapshot(
                host_id=step.host_id,
                target=step.target,
                container_id=summary.id,
                started_at=summary.started_at,
            )
        )
    return snapshots, unread


def make(deps: GraphDeps) -> Node:
    async def ask(state: IncidentState, proposal: Proposal) -> ApprovalRecord | None:
        """Store, publish and send the question; None when a target cannot be read."""
        incident_id = state["incident_id"]
        snapshots, unread = await _snapshots(deps, proposal)
        if unread:
            text = "The plan was not put to you, a target could not be read: " + "; ".join(unread)
            await deps.bus.publish(incident_id, "notice", {"text": text + ". Planning again."})
            return None
        request = await deps.approvals.request(incident_id, proposal, snapshots)
        await deps.bus.publish(
            incident_id,
            "approval_requested",
            {"approval_id": request.id, "plan_hash": request.plan_hash},
        )
        incident = await deps.incidents.get(incident_id)
        if incident is not None:
            await deps.notifier.proposal(incident, state.get("diagnosis"), proposal, request)
        return await deps.approvals.current(incident_id)

    async def approval(state: IncidentState) -> dict:
        incident_id = state["incident_id"]
        proposal = state.get("proposal")
        if proposal is None or proposal.kind != "act":
            return {"decision": None, "grant": None}  # nothing to approve: route goes to report

        record = await deps.approvals.current(incident_id)
        consumed = state.get("approval")
        if record is None or (consumed is not None and record.id == consumed.id):
            record = await ask(state, proposal)
            if record is None:
                return {"decision": None, "grant": None}

        decision = record.decision  # set when it was decided while the run was not waiting
        if decision is None:
            asked = {"approval_id": record.id, "proposal": proposal.model_dump(mode="json")}
            resumed = Decision.model_validate(interrupt(asked))
            decision = await deps.approvals.resolve(incident_id, resumed)
            if decision is None:  # another channel won first; its decision stands
                latest = await deps.approvals.current(incident_id)
                assert latest is not None and latest.decision is not None
                decision = latest.decision

        await deps.bus.publish(
            incident_id,
            "decision",
            {"kind": decision.kind, "text": decision.text, "via": decision.via},
        )
        approved = decision.kind == "approve"
        update: dict = {
            "approval": record.request,
            "decision": decision,
            "grant": await deps.approvals.grant(incident_id) if approved else None,
        }
        if decision.kind == "instruct" and decision.text:
            update["instructions"] = [*state.get("instructions", []), decision.text]
        return update

    return approval


def route(state: IncidentState) -> Literal["propose", "report"]:
    """An instruction goes back to Propose, and so does a plan that could not be put to the
    human; a no closes with a report.

    A yes goes to the report too until Execute exists (Milestone 3), which reports the plan as
    approved but unverified.
    """
    proposal = state.get("proposal")
    if proposal is None or proposal.kind != "act":
        return "report"
    decision = state.get("decision")
    return "propose" if decision is None or decision.kind == "instruct" else "report"
