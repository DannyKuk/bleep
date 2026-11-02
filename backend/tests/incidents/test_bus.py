import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from bleep.incidents.bus import EventBus
from bleep.incidents.repository import IncidentRepo
from bleep.schemas.incidents import Signal, Subject
from bleep.store.db import Database

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


@pytest.fixture
async def parts(tmp_path):
    db = Database(tmp_path / "bleep.sqlite")
    await db.migrate()
    ticks = iter(range(10_000))

    def clock():
        return T0 + timedelta(seconds=next(ticks))

    repo = IncidentRepo(db, clock=clock)
    ids = []
    for name in ("a", "b"):
        subject = Subject(kind="service", host_id="h1", service_id=name)
        incident, _ = await repo.open_or_attach(
            subject, [Signal(subject=subject, kind="oom", at=T0)]
        )
        ids.append(incident.id)
    return EventBus(db, clock=clock, queue_size=4), ids


async def take(stream, n):
    return [await anext(stream) for _ in range(n)]


async def test_events_persist_with_increasing_seq(parts):
    bus, (a, b) = parts
    e1 = await bus.publish(a, "node_started", {"node": "gather"})
    e2 = await bus.publish(a, "node_finished", {"node": "gather"})
    other = await bus.publish(b, "notice", {})
    assert (e1.seq, e2.seq, other.seq) == (1, 2, 1)
    history = await bus.history(a)
    assert [(e.seq, e.kind) for e in history] == [(1, "node_started"), (2, "node_finished")]
    assert history[0].payload == {"node": "gather"}
    assert [e.seq for e in await bus.history(a, after_seq=1)] == [2]


async def test_concurrent_publishes_get_distinct_seq(parts):
    bus, (a, _) = parts
    events = await asyncio.gather(*(bus.publish(a, "notice", {"i": i}) for i in range(8)))
    assert sorted(e.seq for e in events) == list(range(1, 9))


async def test_two_subscribers_both_receive(parts):
    bus, (a, b) = parts
    async with bus.subscribe(a) as one, bus.subscribe(a) as two, bus.subscribe(None) as every:
        await bus.publish(a, "notice", {"n": 1})
        await bus.publish(b, "notice", {"n": 2})
        await bus.publish(a, "notice", {"n": 3})
        assert [e.payload["n"] for e in await take(one, 2)] == [1, 3]
        assert [e.payload["n"] for e in await take(two, 2)] == [1, 3]
        assert [e.payload["n"] for e in await take(every, 3)] == [1, 2, 3]


async def test_slow_subscriber_does_not_block_and_history_fills_gap(parts):
    bus, (a, _) = parts
    async with bus.subscribe(a) as stream:
        for i in range(10):  # queue_size is 4; nobody is reading
            await asyncio.wait_for(bus.publish(a, "notice", {"i": i}), timeout=2)
        seqs = [e.seq for e in await take(stream, 4)]
    assert seqs == [7, 8, 9, 10]  # newest kept, oldest dropped
    assert [e.seq for e in await bus.history(a)] == list(range(1, 11))


async def test_event_between_subscribe_and_history_is_seen_once(parts):
    bus, (a, _) = parts
    await bus.publish(a, "notice", {"n": 1})
    async with bus.subscribe(a) as stream:
        await bus.publish(a, "notice", {"n": 2})  # lands after subscribing, before history
        seen = await bus.history(a)
        last = max(e.seq for e in seen)
        await bus.publish(a, "notice", {"n": 3})
        delivered = [e.seq for e in seen]
        for _ in range(2):  # live queue holds seq 2 (duplicate of history) and 3
            event = await asyncio.wait_for(anext(stream), 1)
            if event.seq > last:
                delivered.append(event.seq)
                last = event.seq
    assert delivered == [1, 2, 3]


async def test_concurrent_publishes_reach_subscribers_in_seq_order(parts):
    bus, (a, _) = parts
    bus.queue_size = 64
    async with bus.subscribe(a) as one, bus.subscribe(None) as two:
        await asyncio.gather(*(bus.publish(a, "notice", {"i": i}) for i in range(20)))
        for stream in (one, two):
            seqs = [e.seq for e in await take(stream, 20)]
            assert seqs == list(range(1, 21))


async def test_cancel_removes_subscriber(parts):
    bus, (a, _) = parts

    async def consume():
        async with bus.subscribe(a) as stream:
            await anext(stream)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    assert bus.subscriber_count == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert bus.subscriber_count == 0


async def test_exit_removes_subscriber(parts):
    bus, (a, _) = parts
    async with bus.subscribe(None):
        assert bus.subscriber_count == 1
    assert bus.subscriber_count == 0
