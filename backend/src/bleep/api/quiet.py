"""Snooze or leave alone a service or a host, and end it (#220). See docs/design/incidents.md.

The store tells the watcher, which closes the target's open incidents when a quiet starts and
looks at the target again when it ends; these routes only validate, call the store and answer.
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Response
from pydantic import ValidationError

from bleep.api.deps import get_parts
from bleep.api.errors import Refusal, problems_of
from bleep.incidents.quiet import MAX_SNOOZE
from bleep.schemas.api import QuietView
from bleep.schemas.quiet import QuietRequest, QuietScope
from bleep.schemas.refusal import Problem
from bleep.wiring import AppParts

router = APIRouter(prefix="/quiet", tags=["quiet"])

Parts = Annotated[AppParts, Depends(get_parts)]


@router.put("/services/{service_id:path}")
async def quiet_service(service_id: str, body: Annotated[Any, Body()], parts: Parts) -> QuietView:
    request = _checked(body, parts.quiet.clock())
    if parts.registry.service(service_id) is None:
        raise Refusal(404, "no_service")
    return await _set(parts, "service", service_id, request)


@router.delete("/services/{service_id:path}", status_code=204)
async def end_service_quiet(service_id: str, parts: Parts) -> Response:
    await parts.quiet.clear("service", service_id)
    return Response(status_code=204)


@router.put("/hosts/{host_id}")
async def quiet_host(host_id: str, body: Annotated[Any, Body()], parts: Parts) -> QuietView:
    request = _checked(body, parts.quiet.clock())
    if host_id not in {h.config.id for h in parts.registry.hosts()}:
        raise Refusal(404, "no_host")
    return await _set(parts, "host", host_id, request)


@router.delete("/hosts/{host_id}", status_code=204)
async def end_host_quiet(host_id: str, parts: Parts) -> Response:
    await parts.quiet.clear("host", host_id)
    return Response(status_code=204)


async def _set(
    parts: AppParts, scope: QuietScope, target_id: str, request: QuietRequest
) -> QuietView:
    state = await parts.quiet.set(scope, target_id, request.mode, request.until)
    return QuietView.of(state)


def _problem(kind: str, message: str) -> Refusal:
    return Refusal.invalid([Problem(loc=("body", "until"), type=kind, msg=message, ctx={})])


def _checked(body: Any, now: datetime) -> QuietRequest:
    """The body as a request, or 422 naming the field; never echoes a value sent."""
    try:
        request = QuietRequest.model_validate(body)
    except ValidationError as error:
        problems = [
            Problem(
                loc=("body", *(part for part in problem.loc if part in QuietRequest.model_fields)),
                type=problem.type,
                msg=problem.msg,
                ctx=problem.ctx,
            )
            for problem in problems_of(error)
        ]
        raise Refusal.invalid(problems) from None
    if request.mode == "leave_alone":
        if request.until is not None:
            raise _problem("quiet_leave_alone_until", "leave alone has no end time; send no until")
        return request
    if request.until is None:
        raise _problem("quiet_until_needed", "a snooze needs an until")
    if request.until.tzinfo is None:
        raise _problem("quiet_until_zone", "until needs a time zone")
    if request.until <= now:
        raise _problem("quiet_until_past", "until must be in the future")
    if request.until > now + MAX_SNOOZE:
        raise _problem("quiet_until_far", "until is at most 7 days ahead")
    return request
