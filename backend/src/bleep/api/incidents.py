"""Incident routes: the list, one incident with its findings, its timeline, the dashboard's
decision on its approval, the human's feedback on its findings, and the self-recoveries that never
became incidents."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from bleep.api.deps import get_expectations, get_parts, get_runner
from bleep.guard.redact import redacted_proposal
from bleep.incidents.expectations import Expectations
from bleep.incidents.repository import utcnow
from bleep.incidents.runner import IncidentRunner
from bleep.schemas.api import (
    ApprovalStatus,
    ApprovalView,
    DecisionBody,
    DecisionConflict,
    FeedbackBody,
    IncidentDetail,
    IncidentListItem,
    IncidentVotes,
)
from bleep.schemas.graph import ApprovalRecord, Decision, Feedback
from bleep.schemas.incidents import (
    TERMINAL_STATUSES,
    Incident,
    IncidentEvent,
    IncidentStatus,
    RecoveryEvent,
)
from bleep.wiring import AppParts

router = APIRouter(tags=["incidents"])

Parts = Annotated[AppParts, Depends(get_parts)]
MAX_PAGE = 200

_STATUS: dict[str, ApprovalStatus] = {
    "approve": "approved",
    "reject": "rejected",
    "instruct": "instructed",
}
_CHANNEL = {"telegram": "Telegram", "dashboard": "the dashboard"}
_FINDING = {"diagnosis": "diagnosis", "proposal": "plan"}


async def _incident(parts: AppParts, incident_id: str) -> Incident:
    incident = await parts.incidents.get(incident_id)
    if incident is None:
        raise HTTPException(404, "no such incident")
    return incident


def _approval_status(record: ApprovalRecord) -> ApprovalStatus:
    if record.decision is not None:
        return _STATUS[record.decision.kind]
    return "closed" if record.closed else "pending"


def _approval(record: ApprovalRecord | None) -> ApprovalView | None:
    if record is None:
        return None
    return ApprovalView(
        id=record.id,
        status=_approval_status(record),
        plan_hash=record.plan_hash,
        proposal=redacted_proposal(record.proposal),
        requested_at=record.requested_at,
        reminders=record.reminders,
        decision=record.decision,
    )


def _conflict(detail: str, decision: Decision | None = None) -> JSONResponse:
    body = DecisionConflict(detail=detail, decided_via=decision.via if decision else None)
    return JSONResponse(body.model_dump(), status_code=409)


def _already_decided(decision: Decision) -> JSONResponse:
    return _conflict(f"Already decided on {_CHANNEL[decision.via]}", decision)


@router.get("/incidents")
async def list_incidents(
    parts: Parts,
    status: IncidentStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    service_id: str | None = None,
    fold_of: str | None = None,
    host_id: str | None = None,
) -> list[IncidentListItem]:
    """Newest first, each with the human's votes on its diagnosis and plan and the size of its
    group of repeats; `service_id` keeps one service's (its page lists its past incidents),
    `fold_of` the repeats of one incident, `host_id` a host's own, not its services' (its page
    says when the host is still unreachable after its last one)."""
    incidents = await parts.incidents.list(
        status, limit, offset, service_id, fold_of, host_id=host_id
    )
    votes = await parts.memory.votes([i.id for i in incidents])
    groups = await parts.incidents.groups(incidents)
    return [
        IncidentListItem(
            **incident.model_dump(),
            votes=IncidentVotes(**votes.get(incident.id, {})),
            repeats=groups[incident.id].count if incident.id in groups else 1,
        )
        for incident in incidents
    ]


@router.get("/incidents/{incident_id}")
async def get_incident(
    incident_id: str,
    parts: Parts,
    runner: Annotated[IncidentRunner, Depends(get_runner)],
    expectations: Annotated[Expectations, Depends(get_expectations)],
) -> IncidentDetail:
    incident = await _incident(parts, incident_id)
    state = await runner.state(incident_id)
    expectation = await expectations.offered(incident_id)
    groups = await parts.incidents.groups([incident])
    return IncidentDetail(
        incident=incident,
        diagnosis=state.get("diagnosis"),
        proposal=redacted_proposal(proposal) if (proposal := state.get("proposal")) else None,
        approval=_approval(await parts.approvals.current(incident_id)),
        outcome=state.get("outcome"),
        report=state.get("report"),
        feedback=await parts.memory.feedback(incident_id),
        expectation=expectation.sentence() if expectation is not None else None,
        repeats=group.count if (group := groups.get(incident_id)) is not None else 1,
    )


@router.post(
    "/incidents/{incident_id}/expected",
    responses={409: {"description": "Not offered now, or the plan was decided elsewhere"}},
)
async def mark_expected(
    incident_id: str,
    parts: Parts,
    expectations: Annotated[Expectations, Depends(get_expectations)],
) -> dict[str, str]:
    """ "This is expected": record what makes the next identical event quiet (a job's clean exit,
    a host that sleeps) and close the incident. A pending plan is rejected first, first wins."""
    await _incident(parts, incident_id)
    answer = await expectations.expected(incident_id, "dashboard")
    if not answer.done:
        raise HTTPException(409, answer.text)
    return {"text": answer.text}


@router.get("/incidents/{incident_id}/events")
async def incident_events(
    incident_id: str, parts: Parts, after: Annotated[int, Query(ge=0)] = 0
) -> list[IncidentEvent]:
    """The incident's timeline after seq `after`, in seq order."""
    await _incident(parts, incident_id)
    return await parts.bus.history(incident_id, after)


@router.post(
    "/incidents/{incident_id}/decision",
    responses={409: {"model": DecisionConflict, "description": "Not decided by this request"}},
)
async def decide(
    incident_id: str,
    body: DecisionBody,
    parts: Parts,
    runner: Annotated[IncidentRunner, Depends(get_runner)],
) -> Decision:
    """Decide the approval the dashboard showed. The first answer from any channel wins; a later
    one gets 409 naming the channel that won, and the run resumes only for the winner."""
    incident = await _incident(parts, incident_id)
    record = await parts.approvals.current(incident_id)
    if record is None:
        return _conflict("Nothing is waiting for a decision")
    if record.id != body.approval_id:
        return _conflict("That plan was replaced by a newer one; reload to see it")
    if record.decision is not None:
        return _already_decided(record.decision)
    if incident.status in TERMINAL_STATUSES:
        return _conflict(f"The incident is {incident.status}")

    if body.kind == "approve" and record.proposal.refused:
        return _conflict("Bleep's checks refused this plan; tell Bleep what to do instead")

    decision = Decision(kind=body.kind, text=body.text, via="dashboard", at=utcnow())
    won = await parts.approvals.resolve(incident_id, decision, body.approval_id)
    if won is None:  # another channel answered, or the incident closed, since the read above
        latest = await parts.approvals.current(incident_id)
        if latest is not None and latest.id == body.approval_id and latest.decision is not None:
            return _already_decided(latest.decision)
        if latest is not None and latest.id == body.approval_id and latest.closed:
            return _conflict("The incident was closed before anyone answered")
        return _conflict("That plan was replaced by a newer one; reload to see it")
    await runner.resume(incident_id, won, body.approval_id)
    return won


@router.post(
    "/incidents/{incident_id}/feedback",
    responses={409: {"description": "The run has no diagnosis or plan to rate yet"}},
)
async def give_feedback(
    incident_id: str,
    body: FeedbackBody,
    parts: Parts,
    runner: Annotated[IncidentRunner, Depends(get_runner)],
) -> Feedback:
    """A thumbs up or down on the incident's diagnosis or plan. A later vote on the same one
    replaces it; the incident's memory record gets the votes, now or when Report writes it."""
    await _incident(parts, incident_id)
    if (await runner.state(incident_id)).get(body.target) is None:
        raise HTTPException(409, f"There is no {_FINDING[body.target]} to rate yet")
    return await parts.memory.set_feedback(incident_id, body.target, body.vote, body.note)


@router.get("/recoveries")
async def list_recoveries(
    parts: Parts, limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50
) -> list[RecoveryEvent]:
    """Containers that died and came back inside the grace window, newest first."""
    return await parts.incidents.recoveries(limit)
