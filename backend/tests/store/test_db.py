import pytest

from bleep.store.db import Database, MigrationError


@pytest.fixture
def tmp_db(tmp_path) -> Database:
    return Database(tmp_path / "data" / "bleep.sqlite")


async def applied(db: Database) -> list[int]:
    conn = await db.connect()
    try:
        async with conn.execute("SELECT version FROM schema_version ORDER BY version") as cursor:
            return [row[0] for row in await cursor.fetchall()]
    finally:
        await conn.close()


async def version(db: Database) -> int:
    """The highest version applied, checking that every one up to it was applied once."""
    versions = await applied(db)
    assert versions == list(range(1, len(versions) + 1))
    return versions[-1]


async def test_migrate_is_idempotent(tmp_db):
    await tmp_db.migrate()
    await tmp_db.migrate()
    # Numbers can have gaps while tasks land in parallel (0008 before 0005), so compare the sets.
    assert await applied(tmp_db) == [number for number, _ in tmp_db._migration_files()]


async def test_connect_sets_pragmas(tmp_db):
    conn = await tmp_db.connect()
    try:
        async with conn.execute("PRAGMA foreign_keys") as cursor:
            assert (await cursor.fetchone())[0] == 1
        async with conn.execute("PRAGMA journal_mode") as cursor:
            assert (await cursor.fetchone())[0] == "wal"
    finally:
        await conn.close()


async def test_migration_creates_settings_table(tmp_db):
    await tmp_db.migrate()
    conn = await tmp_db.connect()
    try:
        await conn.execute("INSERT INTO settings VALUES ('k', 'v', 'now')")
        async with conn.execute("SELECT value FROM settings WHERE key='k'") as cursor:
            assert (await cursor.fetchone())[0] == "v"
    finally:
        await conn.close()


def make_migrations(root, files: dict[str, str]):
    root.mkdir(exist_ok=True)
    for name, sql in files.items():
        (root / name).write_text(sql, encoding="utf-8")
    return root


async def tables(db: Database) -> set[str]:
    conn = await db.connect()
    try:
        async with conn.execute("SELECT name FROM sqlite_master WHERE type='table'") as cursor:
            return {row[0] for row in await cursor.fetchall()}
    finally:
        await conn.close()


async def test_migrations_apply_in_order_and_skip_applied(tmp_path):
    root = make_migrations(
        tmp_path / "m",
        {
            "0002_b.sql": "INSERT INTO a VALUES (2);",
            "0001_a.sql": "CREATE TABLE a (n INTEGER);\nINSERT INTO a VALUES (1);",
        },
    )
    db = Database(tmp_path / "db.sqlite", root)
    await db.migrate()
    await db.migrate()
    conn = await db.connect()
    try:
        async with conn.execute("SELECT n FROM a ORDER BY rowid") as cursor:
            assert [row[0] for row in await cursor.fetchall()] == [1, 2]
    finally:
        await conn.close()
    assert await version(db) == 2


async def test_new_migration_applies_on_existing_database(tmp_path):
    root = make_migrations(tmp_path / "m", {"0001_a.sql": "CREATE TABLE a (n INTEGER);"})
    db = Database(tmp_path / "db.sqlite", root)
    await db.migrate()
    make_migrations(root, {"0002_b.sql": "CREATE TABLE b (n INTEGER);"})
    await db.migrate()
    assert {"a", "b"} <= await tables(db)
    assert await version(db) == 2


async def test_failed_migration_leaves_nothing_and_can_be_retried(tmp_path):
    root = make_migrations(
        tmp_path / "m",
        {
            "0001_a.sql": "CREATE TABLE a (n INTEGER);",
            "0002_b.sql": "CREATE TABLE b (n INTEGER);\nINSERT INTO missing VALUES (1);",
        },
    )
    db = Database(tmp_path / "db.sqlite", root)
    with pytest.raises(MigrationError, match="0002_b.sql"):
        await db.migrate()
    assert "b" not in await tables(db)
    assert await version(db) == 1
    make_migrations(root, {"0002_b.sql": "CREATE TABLE b (n INTEGER);"})
    await db.migrate()
    assert "b" in await tables(db)
    assert await version(db) == 2


async def test_duplicate_numbers_rejected(tmp_path):
    root = make_migrations(
        tmp_path / "m", {"0001_a.sql": "SELECT 1;", "0001_other.sql": "SELECT 1;"}
    )
    with pytest.raises(MigrationError, match="0001"):
        await Database(tmp_path / "db.sqlite", root).migrate()


async def test_badly_named_file_rejected(tmp_path):
    root = make_migrations(tmp_path / "m", {"init.sql": "SELECT 1;"})
    with pytest.raises(MigrationError, match="init.sql"):
        await Database(tmp_path / "db.sqlite", root).migrate()


async def test_a_connect_cancelled_midway_leaves_no_connection_running(tmp_db, monkeypatch):
    import asyncio

    import aiosqlite.core

    opened = []
    real_init = aiosqlite.core.Connection.__init__

    def tracking_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        opened.append(self)

    monkeypatch.setattr(aiosqlite.core.Connection, "__init__", tracking_init)
    for step in range(20):  # cancel at every stage of the connect
        task = asyncio.create_task(tmp_db.connect())
        await asyncio.sleep(step * 0.0002)
        task.cancel()
        [result] = await asyncio.gather(task, return_exceptions=True)
        if not isinstance(result, BaseException):  # too late to cancel: it connected
            await result.close()
    await asyncio.sleep(0.05)
    assert opened
    assert [c for c in opened if c._thread.is_alive()] == []
