"""Incident routes: the list, one incident with its findings, its timeline, the dashboard's
decision on its approval, the human's feedback on its findings, and the self-recoveries that never
became incidents.

Bleep's own fixed texts in these views (notices, the report's outcome and timeline, "This is
expected") are shown in the language the person picked; what is stored stays English (#427).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from bleep import prompts
from bleep.api.deps import get_expectations, get_parts, get_runner
from bleep.api.errors import Refusal
from bleep.guard.redact import redacted_proposal
from bleep.incidents.expectations import Expectations
from bleep.incidents.repository import utcnow
from bleep.incidents.runner import IncidentRunner
from bleep.incidents.shown import decision_in, event_in, report_in
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
from bleep.schemas.shown import Shown
from bleep.wiring import AppParts

router = APIRouter(tags=["incidents"])

Parts = Annotated[AppParts, Depends(get_parts)]
MAX_PAGE = 200

_STATUS: dict[str, ApprovalStatus] = {
    "approve": "approved",
    "reject": "rejected",
    "instruct": "instructed",
}


async def _incident(parts: AppParts, incident_id: str) -> Incident:
    incident = await parts.incidents.get(incident_id)
    if incident is None:
        raise Refusal(404, "no_incident")
    return incident


def _approval_status(record: ApprovalRecord) -> ApprovalStatus:
    if record.decision is not None:
        return _STATUS[record.decision.kind]
    return "closed" if record.closed else "pending"


def _approval(record: ApprovalRecord | None, language: str) -> ApprovalView | None:
    if record is None:
        return None
    decision = record.decision
    return ApprovalView(
        id=record.id,
        status=_approval_status(record),
        plan_hash=record.plan_hash,
        proposal=redacted_proposal(record.proposal),
        requested_at=record.requested_at,
        reminders=record.reminders,
        decision=decision_in(decision, language) if decision is not None else None,
    )


def _conflict(template: str, decision: Decision | None = None, **values: str | Shown) -> Refusal:
    """409 shaped as a `DecisionConflict`: the text in the person's language, and the channel
    that answered first."""
    return Refusal(
        409, template, extra={"decided_via": decision.via if decision else None}, **values
    )


def _already_decided(decision: Decision) -> Refusal:
    return _conflict("already_decided", decision, channel=Shown.of(f"channel/on_{decision.via}"))


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
    kinds = await parts.incidents.kinds(incidents)
    return [
        IncidentListItem(
            **incident.model_dump(),
            votes=IncidentVotes(**votes.get(incident.id, {})),
            repeats=groups[incident.id].count if incident.id in groups else 1,
            kind=kinds.get(incident.id),
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
    language = parts.settings_store.language()
    return IncidentDetail(
        incident=incident,
        diagnosis=state.get("diagnosis"),
        proposal=redacted_proposal(proposal) if (proposal := state.get("proposal")) else None,
        approval=_approval(await parts.approvals.current(incident_id), language),
        outcome=state.get("outcome"),
        report=report_in(report, language) if (report := state.get("report")) else None,
        feedback=await parts.memory.feedback(incident_id),
        expectation=prompts.show(expectation.said(), language) if expectation else None,
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
    said = prompts.show(answer.said, parts.settings_store.language())
    if not answer.done:
        raise HTTPException(409, said)
    return {"text": said}


@router.get("/incidents/{incident_id}/events")
async def incident_events(
    incident_id: str, parts: Parts, after: Annotated[int, Query(ge=0)] = 0
) -> list[IncidentEvent]:
    """The incident's timeline after seq `after`, in seq order."""
    await _incident(parts, incident_id)
    language = parts.settings_store.language()
    return [event_in(event, language) for event in await parts.bus.history(incident_id, after)]


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
        raise _conflict("nothing_waiting")
    if record.id != body.approval_id:
        raise _conflict("plan_replaced")
    if record.decision is not None:
        raise _already_decided(record.decision)
    if incident.status in TERMINAL_STATUSES:
        raise _conflict(f"incident_{incident.status}")

    if body.kind == "approve" and record.proposal.refused:
        raise _conflict("plan_refused")

    decision = Decision(kind=body.kind, text=body.text, via="dashboard", at=utcnow())
    won = await parts.approvals.resolve(incident_id, decision, body.approval_id)
    if won is None:  # another channel answered, or the incident closed, since the read above
        latest = await parts.approvals.current(incident_id)
        if latest is not None and latest.id == body.approval_id and latest.decision is not None:
            raise _already_decided(latest.decision)
        if latest is not None and latest.id == body.approval_id and latest.closed:
            raise _conflict("incident_closed_first")
        raise _conflict("plan_replaced")
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
        raise Refusal(409, f"rate_no_{body.target}")
    return await parts.memory.set_feedback(incident_id, body.target, body.vote, body.note)


@router.get("/recoveries")
async def list_recoveries(
    parts: Parts, limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50
) -> list[RecoveryEvent]:
    """Containers that died and came back inside the grace window, newest first."""
    return await parts.incidents.recoveries(limit)
