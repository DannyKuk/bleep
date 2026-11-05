from datetime import UTC, datetime, timedelta
from typing import get_args

import pytest

from bleep.schemas.api import Health
from bleep.store.db import Database
from bleep.watcher.health_store import HealthStore

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
LATER = T0 + timedelta(minutes=5)
SHOP = "h/compose:shop"


async def store_at(tmp_path) -> HealthStore:
    db = Database(tmp_path / "bleep.sqlite")
    await db.migrate()
    return HealthStore(db)


async def test_a_service_with_no_judgement_has_no_since(tmp_path):
    store = await store_at(tmp_path)
    assert await store.current(SHOP) is None


async def test_since_stays_while_the_health_stays(tmp_path):
    store = await store_at(tmp_path)
    await store.record("h", SHOP, "ok", T0)
    await store.record("h", SHOP, "ok", LATER)
    assert await store.current(SHOP) == ("ok", T0)


async def test_since_moves_when_the_health_changes(tmp_path):
    store = await store_at(tmp_path)
    await store.record("h", SHOP, "ok", T0)
    await store.record("h", SHOP, "down", LATER)
    assert await store.current(SHOP) == ("down", LATER)
    back = LATER + timedelta(minutes=1)
    await store.record("h", SHOP, "ok", back)
    assert await store.current(SHOP) == ("ok", back)


async def test_unknown_is_not_stored_and_does_not_reset_since(tmp_path):
    store = await store_at(tmp_path)
    await store.record("h", SHOP, "unknown", T0)
    assert await store.current(SHOP) is None
    await store.record("h", SHOP, "ok", T0)
    await store.record("h", SHOP, "unknown", LATER)
    await store.record("h", SHOP, "ok", LATER + timedelta(minutes=1))
    assert await store.current(SHOP) == ("ok", T0)


async def test_since_survives_a_restart(tmp_path):
    store = await store_at(tmp_path)
    await store.record("h", SHOP, "ok", T0)
    again = await store_at(tmp_path)  # a new process on the same database
    await again.record("h", SHOP, "ok", LATER)
    assert await again.current(SHOP) == ("ok", T0)


async def test_forgetting_a_host_drops_only_its_services(tmp_path):
    store = await store_at(tmp_path)
    await store.record("h", SHOP, "ok", T0)
    await store.record("g", "g/compose:shop", "down", T0)
    await store.forget_host("h")
    assert await store.current(SHOP) is None
    assert await store.current("g/compose:shop") == ("down", T0)


NO_DATA = {"unknown", "asleep"}


@pytest.mark.parametrize("health", sorted(set(get_args(Health)) - NO_DATA))
async def test_every_judged_health_is_stored(tmp_path, health):
    store = await store_at(tmp_path)
    await store.record("h", SHOP, health, T0)
    assert await store.current(SHOP) == (health, T0)


@pytest.mark.parametrize("health", sorted(NO_DATA))
async def test_no_data_is_not_stored_and_keeps_the_last_judgement(tmp_path, health):
    store = await store_at(tmp_path)
    await store.record("h", SHOP, "ok", T0)
    await store.record("h", SHOP, health, LATER)
    assert await store.current(SHOP) == ("ok", T0)
