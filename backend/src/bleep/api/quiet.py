"""Snooze or leave alone a service or a host, and end it (#220). See docs/design/incidents.md.

The store tells the watcher, which closes the target's open incidents when a quiet starts and
looks at the target again when it ends; these routes only validate, call the store and answer.
"""

from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from pydantic import ValidationError

from bleep.api.deps import get_parts
from bleep.schemas.api import QuietView
from bleep.schemas.quiet import QuietRequest, QuietScope
from bleep.wiring import AppParts

router = APIRouter(prefix="/quiet", tags=["quiet"])

Parts = Annotated[AppParts, Depends(get_parts)]

MAX_SNOOZE = timedelta(days=7)


@router.put("/services/{service_id:path}")
async def quiet_service(service_id: str, body: Annotated[Any, Body()], parts: Parts) -> QuietView:
    request = _checked(body, parts.quiet.clock())
    if parts.registry.service(service_id) is None:
        raise HTTPException(404, "no such service")
    return await _set(parts, "service", service_id, request)


@router.delete("/services/{service_id:path}", status_code=204)
async def end_service_quiet(service_id: str, parts: Parts) -> Response:
    await parts.quiet.clear("service", service_id)
    return Response(status_code=204)


@router.put("/hosts/{host_id}")
async def quiet_host(host_id: str, body: Annotated[Any, Body()], parts: Parts) -> QuietView:
    request = _checked(body, parts.quiet.clock())
    if host_id not in {h.config.id for h in parts.registry.hosts()}:
        raise HTTPException(404, "no such host")
    return await _set(parts, "host", host_id, request)


@router.delete("/hosts/{host_id}", status_code=204)
async def end_host_quiet(host_id: str, parts: Parts) -> Response:
    await parts.quiet.clear("host", host_id)
    return Response(status_code=204)


async def _set(
    parts: AppParts, scope: QuietScope, target_id: str, request: QuietRequest
) -> QuietView:
    state = await parts.quiet.set(scope, target_id, request.mode, request.until)
    return QuietView(scope=state.scope, mode=state.mode, until=state.until, since=state.since)


def _problem(field: str, kind: str, message: str) -> HTTPException:
    return HTTPException(422, [{"type": kind, "loc": ["body", field], "msg": message}])


def _checked(body: Any, now: datetime) -> QuietRequest:
    """The body as a request, or 422 naming the field; never echoes a value sent."""
    try:
        request = QuietRequest.model_validate(body)
    except ValidationError as error:
        detail = [
            {
                "type": problem["type"],
                "loc": [
                    "body",
                    *(part for part in problem["loc"] if part in QuietRequest.model_fields),
                ],
                "msg": problem["msg"],
            }
            for problem in error.errors(include_input=False, include_url=False)
        ]
        raise HTTPException(422, detail) from None
    if request.mode == "leave_alone":
        if request.until is not None:
            raise _problem("until", "value_error", "leave alone has no end time; send no until")
        return request
    if request.until is None:
        raise _problem("until", "missing", "a snooze needs an until")
    if request.until.tzinfo is None:
        raise _problem("until", "value_error", "until needs a time zone")
    if request.until <= now:
        raise _problem("until", "value_error", "until must be in the future")
    if request.until > now + MAX_SNOOZE:
        raise _problem("until", "value_error", "until is at most 7 days ahead")
    return request
