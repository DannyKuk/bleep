from datetime import UTC, datetime
from importlib import resources

from bleep.incidents.repository import IncidentRepo
from bleep.store.db import Database

T0 = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)


async def test_incidents_migrate_without_loss_and_still_read_the_ledger_sums(tmp_path):
    packaged = resources.files("bleep.store") / "migrations"
    old = tmp_path / "migrations"
    old.mkdir()
    for file in packaged.iterdir():
        if file.name.endswith(".sql") and int(file.name[:4]) <= 24:
            (old / file.name).write_text(file.read_text())
    path = tmp_path / "bleep.sqlite"
    await Database(path, migrations=old).migrate()
    db = Database(path, migrations=old)
    conn = await db.connect()
    try:
        for n, status in enumerate(["open", "resolved"]):
            await conn.execute(
                "INSERT INTO incidents (id, kind, host_id, service_id, subject_key, status,"
                " title, opened_at, trace_url, fold_key, cost_usd, tokens)"
                " VALUES (?, 'service', 'a', ?, ?, ?, 'container died', ?, 'http://t/1', 'fk',"
                " 9.9, 77)",
                (f"inc{n}", f"s{n}", f"service:a:s{n}", status, T0.isoformat()),
            )
        await conn.execute(
            "INSERT INTO usage (incident_id, model, tokens_in, tokens_out, usd, day, created_at)"
            " VALUES ('inc0', 'm', 100, 20, 0.5, '2026-11-05', ?)",
            (T0.isoformat(),),
        )
        await conn.commit()
    finally:
        await conn.close()

    await Database(path).migrate()

    repo = IncidentRepo(Database(path))
    first, second = await repo.get("inc0"), await repo.get("inc1")
    assert (first.status, first.title, first.trace_url) == ("open", "container died", "http://t/1")
    assert (first.tokens, first.cost_usd) == (120, 0.5)  # the ledger, not the dropped 77 / 9.9
    assert (second.status, second.tokens, second.cost_usd) == ("resolved", 0, None)
    conn = await Database(path).connect()
    try:
        async with conn.execute("PRAGMA table_info(incidents)") as cursor:
            columns = {row[1] for row in await cursor.fetchall()}
        async with conn.execute("PRAGMA index_list(incidents)") as cursor:
            indexes = {row[1] for row in await cursor.fetchall()}
    finally:
        await conn.close()
    assert not {"tokens", "cost_usd"} & columns
    assert {"incidents_one_open_per_subject", "incidents_status", "incidents_fold"} <= indexes
