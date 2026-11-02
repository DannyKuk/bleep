"""The action log in SQLite: Execute's ledger of every step it ran, so a crash never repeats one.

Execute writes a step `started` before its engine call and `done` or `failed` after it; a step
after a failed one is `skipped`. A resumed run reads the log: a finished step is never run again,
and a `started` one is re-checked against the host before anything else happens (Review Focus 1).
Rows are keyed by incident, plan hash and step index, so a new plan after a loop back to Propose
gets rows of its own. See docs/design/incidents.md.
"""

from collections.abc import Callable
from datetime import datetime

import aiosqlite

from bleep.incidents.repository import utcnow
from bleep.schemas.graph import LoggedStep, PlanStep, StepResult
from bleep.store.db import Database


def _when(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _logged(row: aiosqlite.Row) -> LoggedStep:
    return LoggedStep(
        plan_hash=row["plan_hash"],
        host_id=row["host_id"],
        status=row["status"],
        result=StepResult(
            step_index=row["step_index"],
            action=row["action"],
            target=row["target"],
            ok=row["status"] == "done",
            detail=row["detail"],
            started_at=_when(row["started_at"]),
            finished_at=_when(row["finished_at"]),
        ),
    )


class ActionLog:
    def __init__(self, db: Database, clock: Callable[[], datetime] = utcnow) -> None:
        self.db = db
        self.clock = clock

    async def _write(self, sql: str, args: tuple) -> bool:
        conn = await self.db.connect()
        try:
            cursor = await conn.execute(sql, args)
            await conn.commit()
            return cursor.rowcount == 1
        finally:
            await conn.close()

    async def start(
        self, incident_id: str, plan_hash: str, step_index: int, step: PlanStep
    ) -> datetime | None:
        """Mark the step started, before its engine call; the time written, or None if the step
        already finished (done, failed or skipped) and must not run again.

        Starting a step that is still `started` (a resume chose to run it again) moves its start
        time to now, so the next resume compares against the latest attempt.
        """
        now = self.clock()
        written = await self._write(
            "INSERT INTO action_log (incident_id, plan_hash, step_index, action, host_id, target,"
            " status, started_at) VALUES (?, ?, ?, ?, ?, ?, 'started', ?)"
            " ON CONFLICT (incident_id, plan_hash, step_index) DO UPDATE"
            " SET started_at = excluded.started_at WHERE action_log.status = 'started'",
            (
                incident_id,
                plan_hash,
                step_index,
                step.action,
                step.host_id,
                step.target,
                now.isoformat(),
            ),
        )
        return now if written else None

    async def finish(self, incident_id: str, plan_hash: str, result: StepResult) -> bool:
        """Record a started step as `done` (`result.ok`) or `failed`; False if it was not started.

        The row keeps its own start time, the one written before the engine call."""
        finished = result.finished_at or self.clock()
        return await self._write(
            "UPDATE action_log SET status = ?, detail = ?, finished_at = ?"
            " WHERE incident_id = ? AND plan_hash = ? AND step_index = ? AND status = 'started'",
            (
                "done" if result.ok else "failed",
                result.detail,
                finished.isoformat(),
                incident_id,
                plan_hash,
                result.step_index,
            ),
        )

    async def skip(
        self, incident_id: str, plan_hash: str, step_index: int, step: PlanStep, detail: str
    ) -> bool:
        """Record a step that will not run; False if the step already has a row (a started step
        may have reached the engine, so it is never marked skipped)."""
        return await self._write(
            "INSERT INTO action_log (incident_id, plan_hash, step_index, action, host_id, target,"
            " status, detail) VALUES (?, ?, ?, ?, ?, ?, 'skipped', ?)"
            " ON CONFLICT (incident_id, plan_hash, step_index) DO NOTHING",
            (incident_id, plan_hash, step_index, step.action, step.host_id, step.target, detail),
        )

    async def steps(self, incident_id: str) -> list[LoggedStep]:
        """Every logged step of the incident, in the order the rows were first written."""
        conn = await self.db.connect()
        try:
            async with conn.execute(
                "SELECT * FROM action_log WHERE incident_id = ? ORDER BY rowid", (incident_id,)
            ) as cursor:
                return [_logged(row) for row in await cursor.fetchall()]
        finally:
            await conn.close()
