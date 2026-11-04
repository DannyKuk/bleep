"""Services and hosts a person snoozed or left alone (docs/design/incidents.md)."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Literal

import aiosqlite

from bleep.incidents.repository import utcnow
from bleep.schemas.incidents import Subject
from bleep.schemas.quiet import QuietChange, QuietMode, QuietScope, QuietState
from bleep.store.db import Database

log = logging.getLogger(__name__)

QUIET_TICK_S = 30.0

Listener = Callable[[QuietChange], Awaitable[None]]


def _state(row: aiosqlite.Row) -> QuietState:
    return QuietState(
        scope=row["scope"],
        target_id=row["target_id"],
        mode=row["mode"],
        until=datetime.fromisoformat(row["until"]) if row["until"] else None,
        since=datetime.fromisoformat(row["since"]),
    )


def _is_over(state: QuietState, now: datetime) -> bool:
    return state.until is not None and state.until <= now


class QuietStore:
    def __init__(self, db: Database, clock: Callable[[], datetime] = utcnow) -> None:
        self.db = db
        self.clock = clock
        self._listeners: list[Listener] = []

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    async def _notify(self, kind: Literal["started", "ended"], state: QuietState) -> None:
        change = QuietChange(kind=kind, state=state)
        for listener in self._listeners:
            try:
                await listener(change)
            except Exception:
                log.exception("a quiet listener failed on %s of %s", kind, state.target_id)

    async def set(
        self, scope: QuietScope, target_id: str, mode: QuietMode, until: datetime | None
    ) -> QuietState:
        state = QuietState(
            scope=scope, target_id=target_id, mode=mode, until=until, since=self.clock()
        )
        conn = await self.db.connect()
        try:
            await conn.execute(
                "INSERT INTO quiet (scope, target_id, mode, until, since) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (scope, target_id) DO UPDATE SET "
                "mode = excluded.mode, until = excluded.until, since = excluded.since",
                (
                    scope,
                    target_id,
                    mode,
                    until.isoformat() if until else None,
                    state.since.isoformat(),
                ),
            )
            await conn.commit()
        finally:
            await conn.close()
        await self._notify("started", state)
        return state

    async def clear(self, scope: QuietScope, target_id: str) -> bool:
        conn = await self.db.connect()
        try:
            async with conn.execute(
                "SELECT * FROM quiet WHERE scope = ? AND target_id = ?", (scope, target_id)
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return False
            await conn.execute(
                "DELETE FROM quiet WHERE scope = ? AND target_id = ?", (scope, target_id)
            )
            await conn.commit()
        finally:
            await conn.close()
        await self._notify("ended", _state(row))
        return True

    async def forget_host(self, host_id: str) -> None:
        """Drop a removed host's state and its services' without telling the listeners: a host
        that is gone is not judged again."""
        prefix = f"{host_id}/"
        conn = await self.db.connect()
        try:
            await conn.execute(
                "DELETE FROM quiet WHERE (scope = 'host' AND target_id = ?) "
                "OR (scope = 'service' AND substr(target_id, 1, ?) = ?)",
                (host_id, len(prefix), prefix),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def _all(self) -> list[QuietState]:
        conn = await self.db.connect()
        try:
            async with conn.execute("SELECT * FROM quiet ORDER BY since") as cursor:
                return [_state(row) for row in await cursor.fetchall()]
        finally:
            await conn.close()

    async def active(self) -> list[QuietState]:
        now = self.clock()
        return [state for state in await self._all() if not _is_over(state, now)]

    async def of(self, scope: QuietScope, target_id: str) -> QuietState | None:
        for state in await self.active():
            if (state.scope, state.target_id) == (scope, target_id):
                return state
        return None

    async def for_subject(self, subject: Subject) -> QuietState | None:
        """The host's state wins over the service's own, so the dashboard says "Host left alone"."""
        host = await self.of("host", subject.host_id)
        if host is not None or subject.kind == "host" or subject.service_id is None:
            return host
        return await self.of("service", subject.service_id)

    async def is_quiet(self, subject: Subject) -> bool:
        return await self.for_subject(subject) is not None

    async def expire(self) -> list[QuietState]:
        """Delete the snoozes whose time has passed and tell the listeners, once each."""
        now = self.clock()
        over = [state for state in await self._all() if _is_over(state, now)]
        ended: list[QuietState] = []
        for state in over:
            if await self._delete_if_unchanged(state):
                ended.append(state)
                await self._notify("ended", state)
        return ended

    async def _delete_if_unchanged(self, state: QuietState) -> bool:
        """False when a person set it again meanwhile, so the new state stays."""
        conn = await self.db.connect()
        try:
            cursor = await conn.execute(
                "DELETE FROM quiet WHERE scope = ? AND target_id = ? AND since = ?",
                (state.scope, state.target_id, state.since.isoformat()),
            )
            await conn.commit()
            return cursor.rowcount > 0
        finally:
            await conn.close()

    async def run(self, interval_s: float = QUIET_TICK_S) -> None:
        while True:
            try:
                await self.expire()
            except Exception:
                log.exception("expiring quiet states failed; trying again next tick")
            await asyncio.sleep(interval_s)
