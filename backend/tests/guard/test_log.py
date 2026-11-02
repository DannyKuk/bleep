"""The store-backed guard log."""

from datetime import UTC, datetime

from bleep.guard.log import DETAIL_CHARS, GuardLog
from bleep.store.db import Database

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


async def guard_log(tmp_path) -> GuardLog:
    db = Database(tmp_path / "bleep.sqlite")
    await db.migrate()
    return GuardLog(db, clock=lambda: T0)


async def test_records_are_written_in_order_and_read_back_per_incident(tmp_path):
    log = await guard_log(tmp_path)

    log.record("inc-1", "refused_command", "rm -rf /")
    log.record("inc-2", "budget", "daily cost cap spent")
    log.record("inc-1", "redacted", "1 secret(s) redacted from logs output")
    await log.flush()

    entries = await log.entries("inc-1")
    assert [(e.kind, e.detail) for e in entries] == [
        ("refused_command", "rm -rf /"),
        ("redacted", "1 secret(s) redacted from logs output"),
    ]
    assert entries[0].at == T0
    assert [e.incident_id for e in await log.entries()] == ["inc-1", "inc-2", "inc-1"]


async def test_a_long_detail_is_cut(tmp_path):
    log = await guard_log(tmp_path)

    log.record("inc-1", "refused_command", "x" * 5000)
    await log.flush()

    (entry,) = await log.entries("inc-1")
    assert len(entry.detail) == DETAIL_CHARS


def test_record_outside_an_event_loop_writes_at_once(tmp_path):
    import asyncio

    log = asyncio.run(guard_log(tmp_path))

    log.record("inc-1", "blocked_action", "docker_restart while investigating")

    assert [e.kind for e in asyncio.run(log.entries("inc-1"))] == ["blocked_action"]
