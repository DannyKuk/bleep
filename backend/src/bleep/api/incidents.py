"""Incident routes: the list, one incident with its findings, its timeline, the dashboard's
decision on its approval, and the self-recoveries that never became incidents."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from bleep.api.deps import get_parts, get_runner
from bleep.incidents.repository import utcnow
from bleep.incidents.runner import IncidentRunner
from bleep.schemas.api import ApprovalView, DecisionBody, DecisionConflict, IncidentDetail
from bleep.schemas.graph import ApprovalRecord, Decision
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

_STATUS = {"approve": "approved", "reject": "rejected", "instruct": "instructed"}
_CHANNEL = {"telegram": "Telegram", "dashboard": "the dashboard"}


async def _incident(parts: AppParts, incident_id: str) -> Incident:
    incident = await parts.incidents.get(incident_id)
    if incident is None:
        raise HTTPException(404, "no such incident")
    return incident


def _approval(record: ApprovalRecord | None) -> ApprovalView | None:
    if record is None:
        return None
    return ApprovalView(
        id=record.id,
        status=_STATUS[record.decision.kind] if record.decision else "pending",
        plan_hash=record.plan_hash,
        proposal=record.proposal,
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
) -> list[Incident]:
    """Newest first."""
    return await parts.incidents.list(status, limit, offset)


@router.get("/incidents/{incident_id}")
async def get_incident(
    incident_id: str,
    parts: Parts,
    runner: Annotated[IncidentRunner, Depends(get_runner)],
) -> IncidentDetail:
    incident = await _incident(parts, incident_id)
    state = await runner.state(incident_id)
    return IncidentDetail(
        incident=incident,
        diagnosis=state.get("diagnosis"),
        proposal=state.get("proposal"),
        approval=_approval(await parts.approvals.current(incident_id)),
        outcome=state.get("outcome"),
        report=state.get("report"),
    )


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

    decision = Decision(kind=body.kind, text=body.text, via="dashboard", at=utcnow())
    won = await parts.approvals.resolve(incident_id, decision, body.approval_id)
    if won is None:  # another channel answered between the read above and this write
        latest = await parts.approvals.current(incident_id)
        if latest is not None and latest.id == body.approval_id and latest.decision is not None:
            return _already_decided(latest.decision)
        return _conflict("That plan was replaced by a newer one; reload to see it")
    await runner.resume(incident_id, won, body.approval_id)
    return won


@router.get("/recoveries")
async def list_recoveries(
    parts: Parts, limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50
) -> list[RecoveryEvent]:
    """Containers that died and came back inside the grace window, newest first."""
    return await parts.incidents.recoveries(limit)
