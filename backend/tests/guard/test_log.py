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


async def test_recent_is_newest_first_and_filters_by_kind_and_incident(tmp_path):
    log = await guard_log(tmp_path)
    log.record("inc-1", "refused_command", "rm -rf /")
    log.record("inc-2", "redacted", "1 secret(s) redacted from logs output")
    log.record("inc-1", "redacted", "2 secret(s) redacted from inspect output")
    log.record("inc-1", "budget", "daily cost cap spent")

    assert [e.detail for e in await log.recent(limit=2)] == [
        "daily cost cap spent",
        "2 secret(s) redacted from inspect output",
    ]
    assert [e.incident_id for e in await log.recent(kind="redacted")] == ["inc-1", "inc-2"]
    assert [e.kind for e in await log.recent(incident_id="inc-1")] == [
        "budget",
        "redacted",
        "refused_command",
    ]
    [entry] = await log.recent(incident_id="inc-2", kind="redacted")
    assert entry.detail.startswith("1 secret")
    assert entry.id == 2
    assert await log.recent(incident_id="inc-2", kind="budget") == []


GITHUB_TOKEN = "ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789"


async def test_a_detail_is_redacted_before_it_is_cut_and_written(tmp_path):
    log = await guard_log(tmp_path)
    straddling = "git push " + "x" * (DETAIL_CHARS - 20) + f" https://{GITHUB_TOKEN}@github.com"
    log.record("inc-1", "refused_command", straddling)
    log.record("inc-1", "refused_command", f"curl -H 'Authorization: Bearer {GITHUB_TOKEN}' x")
    await log.flush()

    conn = await log.db.connect()
    try:
        async with conn.execute("SELECT detail FROM guard_log") as cursor:
            stored = [row["detail"] for row in await cursor.fetchall()]
    finally:
        await conn.close()
    assert len(stored) == 2
    assert not [detail for detail in stored if "ghp_" in detail]
    assert all(len(detail) <= DETAIL_CHARS for detail in stored)


def test_a_detail_written_outside_an_event_loop_is_redacted_too(tmp_path):
    import asyncio

    log = asyncio.run(guard_log(tmp_path))
    log.record("inc-1", "refused_command", f"echo {GITHUB_TOKEN}")
    [entry] = asyncio.run(log.entries("inc-1"))
    assert GITHUB_TOKEN not in entry.detail and "<redacted>" in entry.detail


def test_a_record_pending_on_an_old_event_loop_is_written_by_the_next_one(tmp_path):
    import asyncio

    log = asyncio.run(guard_log(tmp_path))
    first_loop = asyncio.new_event_loop()

    async def record_and_leave():
        log.record("inc-1", "budget", "made under the first loop")

    first_loop.run_until_complete(record_and_leave())  # its writer task never gets to run

    async def under_the_second_loop():
        log.record("inc-1", "redacted", "made under the second loop")
        await log.flush()
        return await log.entries("inc-1")

    entries = asyncio.run(under_the_second_loop())
    first_loop.close()

    assert [e.detail for e in entries] == [
        "made under the first loop",
        "made under the second loop",
    ]


def test_flush_under_a_new_event_loop_writes_what_an_old_one_left(tmp_path):
    import asyncio

    log = asyncio.run(guard_log(tmp_path))
    first_loop = asyncio.new_event_loop()

    async def record_and_leave():
        log.record("inc-1", "budget", "left pending")

    first_loop.run_until_complete(record_and_leave())

    async def flush_then_read():
        await log.flush()
        return await log.entries("inc-1")

    assert [e.detail for e in asyncio.run(flush_then_read())] == ["left pending"]
    first_loop.close()
