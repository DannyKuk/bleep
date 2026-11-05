"""Hides the shop reset from the engine the harness hands to Bleep (#110).

Recreating the shop before a run (`ShopReset`) makes the engine emit die, destroy, create and
start events for every container, a few minutes before the fault's first signal. Bleep's
`recent_events` and the watcher read them as history of the incident, which they are not: the
reset is test setup. `ResetSpans` records when each reset ran, and `hide_resets` wraps a
connector so the engine it builds drops the events inside those spans. Events from `fault.sh`
and from the watcher itself come after the span and stay.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from bleep.hosts.registry import Connector
from bleep.schemas.docker import DockerEvent

# Docker stamps events with the engine's clock, this process stamps the span with its own. A
# Docker Desktop VM can drift a couple of seconds from the machine; more would start to hide the
# fault's own first events, which come a moment after the reset returns.
MARGIN = timedelta(seconds=2)


class ResetSpans:
    """The time spans of the resets so far, each widened by `MARGIN` on both sides."""

    def __init__(self) -> None:
        self._spans: list[tuple[datetime, datetime]] = []

    def record(self, start: datetime, end: datetime) -> None:
        self._spans.append((start - MARGIN, end + MARGIN))

    def hides(self, when: datetime) -> bool:
        when = when if when.tzinfo else when.replace(tzinfo=UTC)
        return any(start <= when <= end for start, end in self._spans)


class _ResetFreeEngine:
    """An engine that reads like the one it wraps, minus the events inside a reset span."""

    def __init__(self, engine: Any, spans: ResetSpans) -> None:
        self._engine = engine
        self._spans = spans

    def __getattr__(self, name: str) -> Any:
        return getattr(self._engine, name)

    async def events(self, since: datetime, until: datetime) -> list[DockerEvent]:
        return [e for e in await self._engine.events(since, until) if not self._spans.hides(e.time)]

    async def stream_events(self) -> AsyncIterator[DockerEvent]:
        async for event in self._engine.stream_events():
            if not self._spans.hides(event.time):
                yield event


def hide_resets(connector: Connector, spans: ResetSpans) -> Connector:
    """`connector`, with every engine it builds hiding the events inside `spans`."""

    async def connect(host):  # noqa: ANN001, ANN202
        session, engine = await connector(host)
        return session, _ResetFreeEngine(engine, spans)

    return connect
