"""The event bus: every event is stored first, then fanned out to live subscribers."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
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
        self._subscribers: list[tuple[str | None, asyncio.Queue[IncidentEvent]]] = []

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def publish(
        self, incident_id: str, kind: str, payload: dict[str, Any] | None = None
    ) -> IncidentEvent:
        """Persist the event with the next `seq` of its incident, then fan it out."""
        at = self.clock()
        body = payload or {}
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
        event = IncidentEvent(incident_id=incident_id, seq=seq, kind=kind, payload=body, at=at)
        for target, queue in list(self._subscribers):
            if target is None or target == incident_id:
                if queue.full():
                    queue.get_nowait()  # drop the oldest; the subscriber fills gaps from history
                queue.put_nowait(event)
        return event

    async def subscribe(self, incident_id: str | None) -> AsyncIterator[IncidentEvent]:
        """Live events for one incident, or for all of them with None.

        Registers when iteration starts and unregisters when the iterator is closed or
        cancelled. A subscriber that falls behind loses its oldest events; `history` has them.
        """
        entry: tuple[str | None, asyncio.Queue[IncidentEvent]] = (
            incident_id,
            asyncio.Queue(self.queue_size),
        )
        self._subscribers.append(entry)
        try:
            while True:
                yield await entry[1].get()
        finally:
            self._subscribers.remove(entry)

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
