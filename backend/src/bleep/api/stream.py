"""The live event stream: server-sent events from the bus, for the dashboard's live views.

`GET /api/stream?incident_id=` sends one incident's events: first its history after the cursor,
then live ones, each with `id:` = its seq. A browser that reconnects sends `Last-Event-ID`, and
the stream replays from the next seq. Without `incident_id` it sends every incident's events,
live only and without an id, because seq counts per incident. A comment line goes out every
`PING_S` seconds so proxies keep an idle stream open. See docs/design/app.md.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from sse_starlette import EventSourceResponse, ServerSentEvent

from bleep.api.deps import get_parts
from bleep.api.errors import Refusal
from bleep.incidents.bus import EventBus
from bleep.incidents.shown import event_in
from bleep.schemas.incidents import IncidentEvent
from bleep.wiring import AppParts

router = APIRouter(tags=["stream"])

PING_S = 15


async def follow(
    bus: EventBus, incident_id: str | None, after: int = 0
) -> AsyncIterator[IncidentEvent]:
    """One incident's events after seq `after` and then live, or every incident's live ones.

    Subscribes before reading the history, so nothing published in between is lost. Each
    incident's events come out once and in seq order: a live event already sent is skipped,
    and a gap (the bus drops a slow subscriber's oldest events) is filled from the history.
    """
    last: dict[str, int] = {}
    async with bus.subscribe(incident_id) as live:
        if incident_id is not None:
            last[incident_id] = after
            for event in await bus.history(incident_id, after):
                last[incident_id] = event.seq
                yield event
        async for event in live:
            seen = last.get(event.incident_id)
            if seen is not None and event.seq <= seen:
                continue
            if seen is None or event.seq == seen + 1:
                batch = [event]
            else:
                batch = await bus.history(event.incident_id, seen)
            for item in batch:
                last[item.incident_id] = item.seq
                yield item


@router.get("/stream")
async def stream(
    parts: Annotated[AppParts, Depends(get_parts)],
    incident_id: str | None = None,
    after: Annotated[int, Query(ge=0)] = 0,
    last_event_id: Annotated[int | None, Header(ge=0)] = None,
) -> EventSourceResponse:
    """`Last-Event-ID` (a reconnect) wins over `after` (the first connect); both apply only to
    one incident's stream."""
    if incident_id is not None and await parts.incidents.get(incident_id) is None:
        raise Refusal(404, "no_incident")
    cursor = last_event_id if last_event_id is not None else after
    with_id = incident_id is not None

    async def messages() -> AsyncIterator[ServerSentEvent]:
        async for event in follow(parts.bus, incident_id, cursor):
            shown = event_in(event, parts.settings_store.language())  # a notice in their words
            yield ServerSentEvent(
                data=shown.model_dump_json(), id=str(event.seq) if with_id else None
            )

    return EventSourceResponse(messages(), ping=PING_S)
