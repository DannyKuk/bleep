"""A recovery's shown text survives the store, and a row from before #455 shows as stored."""

from datetime import UTC, datetime
from importlib import resources

from bleep.incidents.repository import IncidentRepo
from bleep.incidents.shown import recovery_in
from bleep.schemas.incidents import RecoveryEvent
from bleep.store.db import Database
from bleep.watcher import manager

T0 = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)


async def test_a_recovery_keeps_its_shown_text_and_the_models_line_none(tmp_path):
    db = Database(tmp_path / "bleep.sqlite")
    await db.migrate()
    repo = IncidentRepo(db)
    fixed = RecoveryEvent(
        host_id="a",
        container="shop-web-1",
        at=T0,
        explanation=manager.HELD_LOOP_EXPLANATION,
        explanation_shown=manager.HELD_LOOP_SHOWN,
    )
    await repo.log_recovery(fixed)
    await repo.log_recovery(
        fixed.model_copy(update={"explanation": "OOM.", "explanation_shown": None})
    )

    model, held = await repo.recoveries()

    assert (held.explanation, held.explanation_shown) == (
        manager.HELD_LOOP_EXPLANATION,
        manager.HELD_LOOP_SHOWN,
    )
    assert model.explanation_shown is None
    assert recovery_in(model, "de").explanation == "OOM."


async def test_a_row_from_before_the_column_and_a_drifted_one_show_their_english(tmp_path):
    packaged = resources.files("bleep.store") / "migrations"
    old = tmp_path / "migrations"
    old.mkdir()
    for file in packaged.iterdir():
        if file.name.endswith(".sql") and int(file.name[:4]) <= 27:
            (old / file.name).write_text(file.read_text())
    path = tmp_path / "bleep.sqlite"
    await Database(path, migrations=old).migrate()
    conn = await Database(path, migrations=old).connect()
    try:
        await conn.execute(
            "INSERT INTO recovery_events (host_id, container, at, explanation)"
            " VALUES ('a', 'shop-web-1', ?, ?)",
            (T0.isoformat(), manager.CAP_SPENT_EXPLANATION),
        )
        await conn.commit()
    finally:
        await conn.close()

    await Database(path).migrate()
    conn = await Database(path).connect()
    try:
        await conn.execute(
            "INSERT INTO recovery_events (host_id, container, at, explanation, explanation_shown)"
            " VALUES ('a', 'shop-db-1', ?, 'came back', '{\"name\": 7}')",
            (T0.isoformat(),),
        )
        await conn.commit()
    finally:
        await conn.close()

    rows = await IncidentRepo(Database(path)).recoveries()

    assert [r.explanation_shown for r in rows] == [None, None]
    assert sorted(recovery_in(r, "de").explanation for r in rows) == [
        manager.CAP_SPENT_EXPLANATION,
        "came back",
    ]
