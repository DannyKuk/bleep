"""The guard log: what the guard refused, blocked or changed, per incident, for the dev panel.

`GuardLogger` is the Protocol tools, Gather, Investigate and the watcher record to; `GuardLog`
keeps the records in the `guard_log` table. `record` stays synchronous so any code can call it;
the write runs as a task on the event loop, and `flush()` waits for the writes in flight.
"""

import asyncio
import logging
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from bleep.store.db import Database

log = logging.getLogger(__name__)

GuardKind = Literal[
    "refused_command", "redacted", "blocked_action", "injection_marker", "budget", "foreign_chat"
]
DETAIL_CHARS = 500


class GuardLogger(Protocol):
    """Where guard events are recorded."""

    def record(self, incident_id: str, kind: GuardKind, detail: str) -> None: ...


@dataclass(frozen=True)
class GuardEntry:
    incident_id: str
    kind: GuardKind
    detail: str
    at: datetime


_INSERT = "INSERT INTO guard_log (incident_id, kind, detail, at) VALUES (?, ?, ?, ?)"


def _now() -> datetime:
    return datetime.now(UTC)


class GuardLog:
    def __init__(self, db: Database, clock: Callable[[], datetime] = _now) -> None:
        self.db = db
        self.clock = clock
        self._pending: list[tuple[str, str, str, str]] = []
        self._writer: asyncio.Task[None] | None = None

    def record(self, incident_id: str, kind: GuardKind, detail: str) -> None:
        row = (incident_id, kind, detail[:DETAIL_CHARS], self.clock().isoformat())
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # no event loop to block: write at once
            with closing(sqlite3.connect(self.db.path)) as conn, conn:
                conn.execute(_INSERT, row)
            return
        self._pending.append(row)
        if self._writer is None or self._writer.done():
            self._writer = loop.create_task(self._write())

    async def _write(self) -> None:
        """One writer drains the queue, so records keep the order they were made in."""
        while self._pending:
            rows, self._pending = self._pending, []
            try:
                conn = await self.db.connect()
                try:
                    await conn.executemany(_INSERT, rows)
                    await conn.commit()
                finally:
                    await conn.close()
            except Exception:
                log.exception("guard log: could not write %d record(s)", len(rows))

    async def flush(self) -> None:
        """Wait until every record made so far is written."""
        while self._writer is not None and not self._writer.done():
            await self._writer

    async def entries(self, incident_id: str | None = None, limit: int = 200) -> list[GuardEntry]:
        """Oldest first: one incident's, or every incident's."""
        await self.flush()
        where, args = ("WHERE incident_id = ?", (incident_id,)) if incident_id else ("", ())
        conn = await self.db.connect()
        try:
            async with conn.execute(
                f"SELECT * FROM guard_log {where} ORDER BY id LIMIT ?", (*args, limit)
            ) as cursor:
                rows = await cursor.fetchall()
        finally:
            await conn.close()
        return [
            GuardEntry(r["incident_id"], r["kind"], r["detail"], datetime.fromisoformat(r["at"]))
            for r in rows
        ]
