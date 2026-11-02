import pytest

from bleep.store.db import Database


@pytest.fixture
def tmp_db(tmp_path) -> Database:
    return Database(tmp_path / "data" / "bleep.sqlite")


async def version(db: Database) -> int:
    conn = await db.connect()
    try:
        async with conn.execute("SELECT MAX(version), COUNT(*) FROM schema_version") as cursor:
            top, count = await cursor.fetchone()
        assert count == top
        return top
    finally:
        await conn.close()


async def test_migrate_is_idempotent(tmp_db):
    await tmp_db.migrate()
    await tmp_db.migrate()
    assert await version(tmp_db) == 1


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
