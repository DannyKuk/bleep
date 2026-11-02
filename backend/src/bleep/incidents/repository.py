"""Incidents and their signals in SQLite. At most one incident is open per subject."""

import secrets
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

import aiosqlite

from bleep.schemas.incidents import (
    TERMINAL_STATUSES,
    Incident,
    IncidentStatus,
    RecoveryEvent,
    Signal,
    Subject,
)
from bleep.store.db import Database

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_incident_id(now: datetime) -> str:
    """`inc_` + 10 chars of millisecond time + 8 random chars: sorts by creation, no dependency."""
    millis = int(now.timestamp() * 1000)
    stamp = "".join(_CROCKFORD[(millis >> shift) & 31] for shift in range(45, -5, -5))
    return "inc_" + stamp + "".join(secrets.choice(_CROCKFORD) for _ in range(8))


def _incident(row: aiosqlite.Row) -> Incident:
    return Incident(
        id=row["id"],
        subject=Subject(kind=row["kind"], host_id=row["host_id"], service_id=row["service_id"]),
        status=row["status"],
        opened_at=datetime.fromisoformat(row["opened_at"]),
        closed_at=datetime.fromisoformat(row["closed_at"]) if row["closed_at"] else None,
        title=row["title"],
        cost_usd=row["cost_usd"],
        tokens=row["tokens"],
    )


def _title(subject: Subject, signals: list[Signal]) -> str:
    what = signals[0].kind.replace("_", " ") if signals else "incident"
    where = subject.service_id or subject.host_id
    return f"{what} on {where}"


_OPEN = f"status NOT IN ({', '.join(repr(s) for s in TERMINAL_STATUSES)})"


class IncidentRepo:
    def __init__(self, db: Database, clock: Callable[[], datetime] = utcnow) -> None:
        self.db = db
        self.clock = clock

    async def open_or_attach(
        self, subject: Subject, signals: list[Signal]
    ) -> tuple[Incident, bool]:
        """Attach `signals` to the subject's open incident, or open one; True when opened.

        A service signal on a host whose open incident has a `host_unreachable` signal attaches
        to that host incident. The write transaction is taken up front and the unique index is
        the backstop, so two concurrent callers for one subject end with one incident.
        """
        conn = await self.db.connect()
        try:
            for attempt in range(2):
                await conn.execute("BEGIN IMMEDIATE")
                try:
                    target = await self._open_target(conn, subject)
                    created = target is None
                    if target is None:
                        target = await self._insert(conn, subject, signals)
                    for signal in signals:
                        await conn.execute(
                            "INSERT INTO incident_signals (incident_id, signal, at)"
                            " VALUES (?, ?, ?)",
                            (target, signal.model_dump_json(), signal.at.isoformat()),
                        )
                    await conn.commit()
                except sqlite3.IntegrityError:
                    await conn.rollback()
                    if attempt:
                        raise
                    continue  # lost a race on the unique index: re-select and attach
                except BaseException:
                    await conn.rollback()
                    raise
                incident = await self._get(conn, target)
                assert incident is not None
                return incident, created
            raise AssertionError("unreachable")
        finally:
            await conn.close()

    async def _open_target(self, conn: aiosqlite.Connection, subject: Subject) -> str | None:
        async with conn.execute(
            f"SELECT id FROM incidents WHERE subject_key = ? AND {_OPEN}", (subject.key,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is not None:
            return row["id"]
        if subject.kind != "service":
            return None
        host_key = Subject(kind="host", host_id=subject.host_id).key
        async with conn.execute(
            f"SELECT i.id FROM incidents i WHERE i.subject_key = ? AND i.{_OPEN}"
            " AND EXISTS (SELECT 1 FROM incident_signals s WHERE s.incident_id = i.id"
            " AND json_extract(s.signal, '$.kind') = 'host_unreachable')",
            (host_key,),
        ) as cursor:
            row = await cursor.fetchone()
        return row["id"] if row is not None else None

    async def _insert(
        self, conn: aiosqlite.Connection, subject: Subject, signals: list[Signal]
    ) -> str:
        now = self.clock()
        incident_id = new_incident_id(now)
        await conn.execute(
            "INSERT INTO incidents (id, kind, host_id, service_id, subject_key, status, title,"
            " opened_at) VALUES (?, ?, ?, ?, ?, 'open', ?, ?)",
            (
                incident_id,
                subject.kind,
                subject.host_id,
                subject.service_id,
                subject.key,
                _title(subject, signals),
                now.isoformat(),
            ),
        )
        return incident_id

    async def set_status(self, incident_id: str, status: IncidentStatus) -> None:
        closed_at = self.clock().isoformat() if status in TERMINAL_STATUSES else None
        conn = await self.db.connect()
        try:
            await conn.execute(
                "UPDATE incidents SET status = ?, closed_at = COALESCE(?, closed_at) WHERE id = ?",
                (status, closed_at, incident_id),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def _get(self, conn: aiosqlite.Connection, incident_id: str) -> Incident | None:
        async with conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)) as cursor:
            row = await cursor.fetchone()
        return _incident(row) if row is not None else None

    async def get(self, incident_id: str) -> Incident | None:
        conn = await self.db.connect()
        try:
            return await self._get(conn, incident_id)
        finally:
            await conn.close()

    async def list(
        self, status: IncidentStatus | None = None, limit: int = 50, offset: int = 0
    ) -> list[Incident]:
        where, args = ("WHERE status = ?", [status]) if status else ("", [])
        conn = await self.db.connect()
        try:
            async with conn.execute(
                f"SELECT * FROM incidents {where}"
                " ORDER BY opened_at DESC, id DESC LIMIT ? OFFSET ?",
                [*args, limit, offset],
            ) as cursor:
                return [_incident(row) for row in await cursor.fetchall()]
        finally:
            await conn.close()

    async def signals(self, incident_id: str) -> list[Signal]:
        conn = await self.db.connect()
        try:
            async with conn.execute(
                "SELECT signal FROM incident_signals WHERE incident_id = ? ORDER BY id",
                (incident_id,),
            ) as cursor:
                return [
                    Signal.model_validate_json(row["signal"]) for row in await cursor.fetchall()
                ]
        finally:
            await conn.close()

    async def log_recovery(self, event: RecoveryEvent) -> RecoveryEvent:
        conn = await self.db.connect()
        try:
            cursor = await conn.execute(
                "INSERT INTO recovery_events (host_id, service_id, container, at, explanation)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    event.host_id,
                    event.service_id,
                    event.container,
                    event.at.isoformat(),
                    event.explanation,
                ),
            )
            await conn.commit()
            return event.model_copy(update={"id": cursor.lastrowid})
        finally:
            await conn.close()

    async def recoveries(self, limit: int = 100) -> list[RecoveryEvent]:
        conn = await self.db.connect()
        try:
            async with conn.execute(
                "SELECT * FROM recovery_events ORDER BY at, id LIMIT ?", (limit,)
            ) as cursor:
                return [
                    RecoveryEvent(
                        id=row["id"],
                        host_id=row["host_id"],
                        service_id=row["service_id"],
                        container=row["container"],
                        at=datetime.fromisoformat(row["at"]),
                        explanation=row["explanation"],
                    )
                    for row in await cursor.fetchall()
                ]
        finally:
            await conn.close()


__all__ = ["IncidentRepo", "new_incident_id", "utcnow"]
