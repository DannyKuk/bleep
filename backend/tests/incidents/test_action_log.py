import sqlite3
from datetime import UTC, datetime, timedelta
from importlib import resources

import pytest

from bleep.incidents.action_log import ActionLog
from bleep.incidents.repository import IncidentRepo
from bleep.schemas.graph import LoggedStep, PlanStep, StepResult
from bleep.schemas.incidents import Signal, Subject
from bleep.store.db import Database

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


def restart(target: str = "shop-web-1") -> PlanStep:
    return PlanStep(action="docker.restart", host_id="a", target=target)


def result(index: int = 0, ok: bool = True, detail: str = "restarted shop-web-1") -> StepResult:
    return StepResult(
        step_index=index,
        action="docker.restart",
        target="shop-web-1",
        ok=ok,
        detail=detail,
        started_at=T0,
        finished_at=T0 + timedelta(minutes=1),
    )


@pytest.fixture
async def env(tmp_path):
    db = Database(tmp_path / "bleep.sqlite")
    await db.migrate()
    subject = Subject(kind="service", host_id="a", service_id="a/compose:shop")
    incident, _ = await IncidentRepo(db).open_or_attach(
        subject, [Signal(subject=subject, kind="container_died", at=T0)]
    )
    clock = Clock()
    return ActionLog(db, clock=clock), incident.id, clock


async def test_a_step_is_started_then_done(env):
    log, inc, _ = env

    started = await log.start(inc, "h1", 0, restart())
    assert started == T0 + timedelta(seconds=1)
    assert await log.steps(inc) == [
        LoggedStep(
            plan_key="h1",
            host_id="a",
            status="started",
            result=StepResult(
                step_index=0,
                action="docker.restart",
                target="shop-web-1",
                ok=False,
                started_at=started,
            ),
        )
    ]

    assert await log.finish(inc, "h1", result()) is True
    [step] = await log.steps(inc)
    assert step.status == "done"
    assert step.result.ok and step.result.detail == "restarted shop-web-1"
    # The ledger keeps its own start time, written before the engine call.
    assert step.result.started_at == started
    assert step.result.finished_at == T0 + timedelta(minutes=1)


async def test_a_failed_step_is_failed(env):
    log, inc, _ = env
    await log.start(inc, "h1", 0, restart())

    await log.finish(inc, "h1", result(ok=False, detail="host unreachable"))

    [step] = await log.steps(inc)
    assert (step.status, step.result.ok, step.result.detail) == (
        "failed",
        False,
        "host unreachable",
    )


async def test_a_finished_step_cannot_be_started_again(env):
    log, inc, _ = env
    await log.start(inc, "h1", 0, restart())
    await log.finish(inc, "h1", result())

    assert await log.start(inc, "h1", 0, restart()) is None
    assert [s.status for s in await log.steps(inc)] == ["done"]


async def test_a_started_step_cannot_be_started_again(env):
    # A started step may have reached the engine: the ledger never lets it run a second time.
    log, inc, _ = env
    first = await log.start(inc, "h1", 0, restart())

    assert await log.start(inc, "h1", 0, restart()) is None
    [step] = await log.steps(inc)
    assert (step.status, step.result.started_at) == ("started", first)


async def test_rows_written_before_the_plan_key_rename_keep_their_key(tmp_path):
    """Migration 0012 renames `plan_hash` to `plan_key`; the rows already there move with it."""
    packaged = resources.files("bleep.store") / "migrations"
    old = tmp_path / "migrations"
    old.mkdir()
    for file in packaged.iterdir():
        if file.name.endswith(".sql") and int(file.name[:4]) < 12:
            (old / file.name).write_text(file.read_text())
    db = Database(tmp_path / "bleep.sqlite", migrations=old)
    await db.migrate()
    incident_id = "inc_before_0012"
    conn = await db.connect()
    try:  # plain SQL: the repository reads columns added by later migrations
        await conn.execute(
            "INSERT INTO incidents (id, kind, host_id, service_id, subject_key, status, title,"
            " opened_at) VALUES (?, 'service', 'a', 'a/compose:shop',"
            " 'service:a:a/compose:shop', 'open', 'container died', ?)",
            (incident_id, T0.isoformat()),
        )
        await conn.execute(
            "INSERT INTO action_log (incident_id, plan_hash, step_index, action, host_id, target,"
            " status, started_at) VALUES (?, 'h1:ap1', 0, 'docker.restart', 'a', 'shop-web-1',"
            " 'started', ?)",
            (incident_id, T0.isoformat()),
        )
        await conn.commit()
    finally:
        await conn.close()

    await Database(tmp_path / "bleep.sqlite").migrate()

    log = ActionLog(Database(tmp_path / "bleep.sqlite"))
    [step] = await log.steps(incident_id)
    assert (step.plan_key, step.status) == ("h1:ap1", "started")
    assert await log.start(incident_id, "h1:ap1", 0, restart()) is None


async def test_finish_changes_only_a_started_step(env):
    log, inc, _ = env
    assert await log.finish(inc, "h1", result()) is False  # never started
    await log.start(inc, "h1", 0, restart())
    await log.finish(inc, "h1", result(ok=False, detail="boom"))

    assert await log.finish(inc, "h1", result()) is False
    assert [s.status for s in await log.steps(inc)] == ["failed"]


async def test_a_step_after_a_failure_is_skipped(env):
    log, inc, _ = env

    assert await log.skip(inc, "h1", 1, restart("shop-db-1"), "step 1 failed") is True

    [step] = await log.steps(inc)
    assert step.status == "skipped"
    assert step.result == StepResult(
        step_index=1, action="docker.restart", target="shop-db-1", ok=False, detail="step 1 failed"
    )


async def test_skip_never_overwrites_a_step_that_started(env):
    log, inc, _ = env
    await log.start(inc, "h1", 0, restart())

    assert await log.skip(inc, "h1", 0, restart(), "skipped") is False
    assert [s.status for s in await log.steps(inc)] == ["started"]


async def test_steps_come_back_in_the_order_they_were_written_across_plans(env):
    log, inc, _ = env
    await log.start(inc, "h1", 0, restart())
    await log.finish(inc, "h1", result(ok=False, detail="boom"))
    await log.skip(inc, "h1", 1, restart("b"), "step 1 failed")
    await log.start(inc, "h2", 0, restart("c"))

    steps = await log.steps(inc)

    assert [(s.plan_key, s.result.step_index, s.status) for s in steps] == [
        ("h1", 0, "failed"),
        ("h1", 1, "skipped"),
        ("h2", 0, "started"),
    ]
    assert await log.steps("inc_other") == []


async def test_a_row_needs_a_real_incident(env):
    log, _, _ = env
    with pytest.raises(sqlite3.IntegrityError):
        await log.start("inc_missing", "h1", 0, restart())
