import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest

from bleep.auth.sessions import LoginLimiter, SessionStore
from bleep.store.db import Database

T0 = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "s.db")
    await database.migrate()
    return database


async def test_token_is_stored_only_as_a_hash(db):
    token = await SessionStore(db).create()
    with closing(sqlite3.connect(db.path)) as conn:
        rows = conn.execute("SELECT * FROM sessions").fetchall()
    assert len(rows) == 1
    assert token not in repr(rows)
    assert len(rows[0][0]) == 64  # sha256 hex


async def test_valid_then_revoked(db):
    store = SessionStore(db)
    token = await store.create()
    assert await store.check(token) is not None
    await store.revoke(token)
    assert await store.check(token) is None


async def test_unknown_token_is_invalid(db):
    assert await SessionStore(db).check("nope") is None


async def test_expires_after_seven_days_without_use(db):
    clock = Clock()
    store = SessionStore(db, clock)
    token = await store.create()
    clock.now = T0 + timedelta(days=7, seconds=1)
    assert await store.check(token) is None


async def test_use_slides_the_expiry_at_most_once_a_minute(db):
    clock = Clock()
    store = SessionStore(db, clock)
    token = await store.create()
    clock.now = T0 + timedelta(seconds=30)
    assert (await store.check(token)).refreshed is False
    clock.now = T0 + timedelta(minutes=2)
    assert (await store.check(token)).refreshed is True
    clock.now = T0 + timedelta(days=7, minutes=1)  # past the first expiry, inside the slid one
    assert await store.check(token) is not None


async def test_expired_rows_are_removed(db):
    clock = Clock()
    store = SessionStore(db, clock)
    await store.create()
    clock.now = T0 + timedelta(days=8)
    await store.create()
    with closing(sqlite3.connect(db.path)) as conn:
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1


def test_limiter_blocks_the_sixth_failure_and_frees_after_the_window():
    clock = Clock()
    limiter = LoginLimiter(clock=clock)
    for _ in range(5):
        assert limiter.retry_after("ip") is None
        limiter.fail("ip")
    assert limiter.retry_after("ip") == 60
    assert limiter.retry_after("other") is None
    clock.now = T0 + timedelta(seconds=30)
    assert limiter.retry_after("ip") == 30
    clock.now = T0 + timedelta(seconds=61)
    assert limiter.retry_after("ip") is None
