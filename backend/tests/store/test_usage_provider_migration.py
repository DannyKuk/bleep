from datetime import datetime
from importlib import resources

from bleep.guard.log import GuardLog
from bleep.llm.usage import CostLedger
from bleep.llm.usage_report import usage_report
from bleep.schemas.settings import Budgets
from bleep.store.db import Database

NOW = datetime(2026, 11, 13, 12, 0)


async def test_rows_from_before_the_provider_column_keep_it_empty_and_report_it_unknown(tmp_path):
    packaged = resources.files("bleep.store") / "migrations"
    old = tmp_path / "migrations"
    old.mkdir()
    for file in packaged.iterdir():
        if file.name.endswith(".sql") and int(file.name[:4]) <= 29:
            (old / file.name).write_text(file.read_text())
    path = tmp_path / "bleep.sqlite"
    await Database(path, migrations=old).migrate()
    conn = await Database(path, migrations=old).connect()
    try:
        await conn.execute(
            "INSERT INTO usage (incident_id, model, tokens_in, tokens_out, usd, day, created_at,"
            " cache_read_tokens, cache_write_tokens)"
            " VALUES ('inc0', 'claude-sonnet-5-5', 1000, 100, 0.5, '2026-11-12', ?, 400, 0)",
            (NOW.isoformat(),),
        )
        await conn.commit()
    finally:
        await conn.close()

    db = Database(path)
    await db.migrate()
    ledger = CostLedger(db, clock=lambda: NOW)
    await ledger.add("inc1", "claude-sonnet-5-5", 10, 1, 0.01, provider="anthropic")

    view = await usage_report(ledger, Budgets(), 7, GuardLog(db).counts_since)

    assert [(m.model, m.provider, m.calls) for m in view.models] == [
        ("claude-sonnet-5-5", "anthropic", 1),
        ("claude-sonnet-5-5", "unknown", 1),
    ]
    assert view.totals.calls == 2
    assert view.totals.cache_read_tokens == 400  # the older row's other columns are untouched
    assert (await ledger.incident_total("inc0")).tokens_in == 1000
