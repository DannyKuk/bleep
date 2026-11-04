"""The event bus: every event is stored first, then fanned out to live subscribers."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import aiosqlite

from bleep.incidents.repository import utcnow
from bleep.schemas.incidents import IncidentEvent
from bleep.store.db import Database

log = logging.getLogger(__name__)


def _event(row: aiosqlite.Row) -> IncidentEvent:
    return IncidentEvent(
        incident_id=row["incident_id"],
        seq=row["seq"],
        kind=row["kind"],
        payload=json.loads(row["payload"]),
        at=datetime.fromisoformat(row["at"]),
    )


class EventBus:
    def __init__(
        self,
        db: Database,
        clock: Callable[[], datetime] = utcnow,
        queue_size: int = 256,
    ) -> None:
        self.db = db
        self.clock = clock
        self.queue_size = queue_size
        self._lock = asyncio.Lock()
        self._subscribers: list[tuple[str | None, asyncio.Queue[IncidentEvent]]] = []

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def publish(
        self, incident_id: str, kind: str, payload: dict[str, Any] | None = None
    ) -> IncidentEvent:
        """Persist the event with the next `seq` of its incident, then fan it out."""
        body = payload or {}
        # One lock for persist plus fan-out, so subscribers see events in seq order.
        async with self._lock:
            at = self.clock()
            seq = await self._persist(incident_id, kind, body, at)
            event = IncidentEvent(incident_id=incident_id, seq=seq, kind=kind, payload=body, at=at)
            for target, queue in list(self._subscribers):
                if target is None or target == incident_id:
                    if queue.full():
                        queue.get_nowait()  # drop the oldest; the subscriber fills from history
                    queue.put_nowait(event)
        return event

    async def _persist(
        self, incident_id: str, kind: str, body: dict[str, Any], at: datetime
    ) -> int:
        conn = await self.db.connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                async with conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM incident_events"
                    " WHERE incident_id = ?",
                    (incident_id,),
                ) as cursor:
                    seq = (await cursor.fetchone())["next"]
                await conn.execute(
                    "INSERT INTO incident_events (incident_id, seq, kind, payload, at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (incident_id, seq, kind, json.dumps(body), at.isoformat()),
                )
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise
        finally:
            await conn.close()
        return seq

    @asynccontextmanager
    async def subscribe(
        self, incident_id: str | None
    ) -> AsyncIterator[AsyncIterator[IncidentEvent]]:
        """Live events for one incident, or for all of them with None.

        Registers on enter and unregisters on exit or cancel. A subscriber that falls behind
        loses its oldest events; `history` has them. The catch-up pattern, with no gap:

            async with bus.subscribe(incident_id) as events:
                seen = await bus.history(incident_id)       # after registering
                last = max((e.seq for e in seen), default=0)
                ...send `seen`, then for each live event with seq > last: send it, update last
        """
        entry: tuple[str | None, asyncio.Queue[IncidentEvent]] = (
            incident_id,
            asyncio.Queue(self.queue_size),
        )
        self._subscribers.append(entry)

        async def events() -> AsyncIterator[IncidentEvent]:
            while True:
                yield await entry[1].get()

        stream = events()
        try:
            yield stream
        finally:
            self._subscribers.remove(entry)
            await stream.aclose()

    async def last_event(self, incident_id: str) -> IncidentEvent | None:
        """The incident's newest event that is not a notice, or None."""
        conn = await self.db.connect()
        try:
            async with conn.execute(
                "SELECT * FROM incident_events WHERE incident_id = ? AND kind != 'notice'"
                " ORDER BY seq DESC LIMIT 1",
                (incident_id,),
            ) as cursor:
                row = await cursor.fetchone()
        finally:
            await conn.close()
        return _event(row) if row else None

    async def last_activity(self, incident_id: str) -> datetime | None:
        """When the incident's run last did something: the time of its newest event that is not a
        notice (a notice only reports on the run). None if it has no such event."""
        conn = await self.db.connect()
        try:
            async with conn.execute(
                "SELECT MAX(at) AS at FROM incident_events"
                " WHERE incident_id = ? AND kind != 'notice'",
                (incident_id,),
            ) as cursor:
                row = await cursor.fetchone()
        finally:
            await conn.close()
        return datetime.fromisoformat(row["at"]) if row and row["at"] else None

    async def history(self, incident_id: str, after_seq: int = 0) -> list[IncidentEvent]:
        conn = await self.db.connect()
        try:
            async with conn.execute(
                "SELECT * FROM incident_events WHERE incident_id = ? AND seq > ? ORDER BY seq",
                (incident_id, after_seq),
            ) as cursor:
                return [_event(row) for row in await cursor.fetchall()]
        finally:
            await conn.close()
