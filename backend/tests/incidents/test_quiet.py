import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from bleep.incidents.quiet import QuietStore
from bleep.schemas.incidents import Subject
from bleep.schemas.quiet import QuietChange
from bleep.store.db import Database

T0 = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
SHOP = Subject(kind="service", host_id="h", service_id="h/compose:shop")
OTHER_HOST_SHOP = Subject(kind="service", host_id="g", service_id="g/compose:shop")
HOUR = timedelta(hours=1)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
async def store(tmp_path, clock):
    db = Database(tmp_path / "bleep.sqlite")
    await db.migrate()
    return QuietStore(db, clock)


def listen(store):
    heard: list[QuietChange] = []

    async def listener(change):
        heard.append(change)

    store.subscribe(listener)
    return heard


async def test_a_snooze_is_quiet_until_its_time_and_then_not(store, clock):
    await store.set("service", "h/compose:shop", "snooze", T0 + HOUR)
    assert await store.is_quiet(SHOP)
    clock.advance(HOUR + timedelta(seconds=1))
    assert not await store.is_quiet(SHOP)
    assert await store.of("service", "h/compose:shop") is None
    assert await store.active() == []


async def test_leave_alone_has_no_end(store, clock):
    await store.set("service", "h/compose:shop", "leave_alone", None)
    clock.advance(timedelta(days=365))
    assert await store.is_quiet(SHOP)


async def test_a_quiet_host_makes_its_services_quiet_but_not_another_hosts(store):
    await store.set("host", "h", "leave_alone", None)
    assert await store.is_quiet(SHOP)
    assert await store.is_quiet(Subject(kind="host", host_id="h"))
    assert not await store.is_quiet(OTHER_HOST_SHOP)


async def test_a_quiet_service_does_not_quiet_its_host(store):
    await store.set("service", "h/compose:shop", "leave_alone", None)
    assert not await store.is_quiet(Subject(kind="host", host_id="h"))


async def test_for_subject_names_the_host_when_both_are_quiet(store, clock):
    await store.set("service", "h/compose:shop", "snooze", T0 + HOUR)
    await store.set("host", "h", "leave_alone", None)
    state = await store.for_subject(SHOP)
    assert state is not None and state.scope == "host"


async def test_setting_again_replaces_the_state(store):
    await store.set("service", "h/compose:shop", "snooze", T0 + HOUR)
    await store.set("service", "h/compose:shop", "leave_alone", None)
    states = await store.active()
    assert len(states) == 1
    assert states[0].mode == "leave_alone" and states[0].until is None


async def test_listeners_hear_started_ended_and_expiry(store, clock):
    heard = listen(store)
    await store.set("service", "h/compose:shop", "leave_alone", None)
    assert [c.kind for c in heard] == ["started"]
    assert await store.clear("service", "h/compose:shop")
    assert [c.kind for c in heard] == ["started", "ended"]

    heard.clear()
    await store.set("service", "h/compose:shop", "snooze", T0 + HOUR)
    clock.advance(HOUR + timedelta(seconds=1))
    expired = await store.expire()
    assert [s.target_id for s in expired] == ["h/compose:shop"]
    assert [c.kind for c in heard] == ["started", "ended"]
    assert await store.expire() == []
    assert [c.kind for c in heard] == ["started", "ended"]


async def test_clear_of_nothing_returns_false_and_tells_nobody(store):
    heard = listen(store)
    assert await store.clear("host", "h") is False
    assert heard == []


async def test_a_failing_listener_does_not_stop_the_others(store):
    async def broken(change):
        raise RuntimeError("boom")

    store.subscribe(broken)
    heard = listen(store)
    await store.set("host", "h", "leave_alone", None)
    assert [c.kind for c in heard] == ["started"]


async def test_run_survives_a_failing_expire_and_keeps_ticking(store, monkeypatch):
    calls = 0

    async def flaky():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("db locked")
        return []

    monkeypatch.setattr(store, "expire", flaky)
    task = asyncio.create_task(store.run(interval_s=0.001))
    for _ in range(200):
        if calls >= 3:
            break
        await asyncio.sleep(0.005)
    task.cancel()
    assert calls >= 3
