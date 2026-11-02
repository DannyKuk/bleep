import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from bleep.hosts.config import ServiceOverrides
from bleep.hosts.engine import EngineUnavailable
from bleep.hosts.registry import Registry
from bleep.schemas.docker import ContainerSummary
from fakes import FakeEngine, ssh_host

T0 = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)


def box(name: str, project: str | None = None) -> ContainerSummary:
    labels = {"com.docker.compose.project": project} if project else {}
    return ContainerSummary(id=f"id-{name}", name=name, image="img", state="running", labels=labels)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


class Connector:
    """Builds a FakeEngine per host; a host in `fail` raises on connect."""

    def __init__(self, engines: dict[str, FakeEngine], fail: set[str] = frozenset()) -> None:
        self.engines = engines
        self.fail = set(fail)
        self.calls: list[str] = []

    async def __call__(self, host):
        self.calls.append(host.id)
        if host.id in self.fail:
            raise EngineUnavailable(host.id, "connection refused")
        return None, self.engines[host.id]


def make(fail=(), clock=None):
    hosts = [
        ssh_host(id="a", name="A", services={"shop": ServiceOverrides(repo="me/shop")}),
        ssh_host(id="b", name="B"),
    ]
    engines = {
        "a": FakeEngine("a", [box("shop-web-1", "shop"), box("shop-db-1", "shop")]),
        "b": FakeEngine("b", [box("redis")]),
    }
    connector = Connector(engines, set(fail))
    return Registry(hosts, connector, clock=clock or Clock()), engines, connector


async def test_start_connects_every_host_and_lists_services():
    clock = Clock()
    reg, _, connector = make(clock=clock)
    await reg.start()
    assert sorted(connector.calls) == ["a", "b"]
    assert all(h.reachable and h.last_seen == T0 and h.error is None for h in reg.hosts())
    assert sorted(s.id for s in reg.services()) == ["a/compose:shop", "b/container:redis"]
    assert [s.id for s in reg.services("a")] == ["a/compose:shop"]
    assert reg.service("a/compose:shop").repo == "me/shop"
    assert reg.service("nope/x") is None


async def test_start_keeps_a_host_that_cannot_connect():
    reg, _, _ = make(fail={"b"})
    await reg.start()
    states = {h.config.id: h for h in reg.hosts()}
    assert states["a"].reachable
    assert not states["b"].reachable
    assert "connection refused" in states["b"].error
    assert states["b"].last_seen is None
    assert reg.services("b") == []
    with pytest.raises(EngineUnavailable):
        reg.engine("b")


async def test_container_lookup_by_name_or_id():
    reg, _, _ = make()
    await reg.start()
    assert reg.container("a", "shop-web-1").id == "id-shop-web-1"
    assert reg.container("a", "id-shop-db-1").name == "shop-db-1"
    assert reg.container("a", "missing") is None
    assert reg.container("zzz", "x") is None


async def test_refresh_failure_keeps_snapshot_and_last_seen():
    clock = Clock()
    reg, engines, _ = make(clock=clock)
    await reg.start()
    engines["a"].drop()
    clock.now = T0 + timedelta(minutes=5)
    await reg.refresh("a")
    state = {h.config.id: h for h in reg.hosts()}["a"]
    assert not state.reachable
    assert state.last_seen == T0
    assert "simulated drop" in state.error
    assert [s.id for s in reg.services("a")] == ["a/compose:shop"]


async def test_refresh_recovers_and_picks_up_changes():
    clock = Clock()
    reg, engines, _ = make(clock=clock)
    await reg.start()
    engines["a"].drop()
    await reg.refresh("a")
    engines["a"].restore()
    engines["a"].container_list.append(box("cache"))
    clock.now = T0 + timedelta(minutes=6)
    await reg.refresh("a")
    state = {h.config.id: h for h in reg.hosts()}["a"]
    assert state.reachable and state.error is None and state.last_seen == clock.now
    assert sorted(s.id for s in reg.services("a")) == ["a/compose:shop", "a/container:cache"]


async def test_reconnect_connects_a_host_that_was_down_at_start():
    reg, _, connector = make(fail={"b"})
    await reg.start()
    connector.fail.clear()
    assert await reg.reconnect("b") is True
    assert [s.id for s in reg.services("b")] == ["b/container:redis"]


async def test_reconnect_reopens_session_then_engine():
    reg, engines, _ = make()
    await reg.start()
    engines["a"].drop()
    await reg.refresh("a")
    engines["a"].restore()
    assert await reg.reconnect("a") is True
    assert engines["a"].reconnects == 1
    assert {h.config.id: h for h in reg.hosts()}["a"].reachable
    assert reg.engine("a") is engines["a"]


async def test_reconnect_failure_is_recorded_not_raised():
    reg, engines, _ = make()
    await reg.start()
    engines["a"].drop()
    assert await reg.reconnect("a") is False
    assert not {h.config.id: h for h in reg.hosts()}["a"].reachable


async def test_self_containers_never_appear():
    reg, engines, _ = make()
    own = box("bleep-api")
    own.labels["bleep.self"] = "true"
    engines["b"].container_list.append(own)
    await reg.start()
    assert [c.name for s in reg.services("b") for c in s.containers] == ["redis"]


async def test_close_closes_engines():
    reg, engines, _ = make()
    await reg.start()
    await reg.close()
    assert engines["a"].closed and engines["b"].closed


async def test_start_survives_an_unexpected_connector_error():
    reg, _, connector = make()

    async def broken(host):
        raise RuntimeError("boom")

    reg._connector = broken
    await reg.start()
    assert all(not h.reachable and "boom" in h.error for h in reg.hosts())


async def test_concurrent_reconnects_connect_once():
    reg, _, connector = make(fail={"b"})
    await reg.start()
    connector.fail.clear()
    connector.calls.clear()
    results = await asyncio.gather(reg.reconnect("b"), reg.reconnect("b"))
    assert results == [True, True]
    assert connector.calls == ["b"]


async def test_after_close_nothing_connects_or_refreshes():
    reg, engines, connector = make(fail={"b"})
    await reg.start()
    await reg.close()
    connector.fail.clear()
    connector.calls.clear()
    engines["a"].restore()
    assert await reg.reconnect("b") is False
    await reg.refresh("a")
    assert connector.calls == []
    assert engines["a"].reconnects == 0


async def test_container_accepts_a_unique_id_prefix_of_12_chars():
    reg, engines, _ = make()
    engines["b"].container_list = [
        ContainerSummary(id="abcdef123456aaaa", name="x", image="i", state="running"),
        ContainerSummary(id="abcdef123456bbbb", name="y", image="i", state="running"),
        ContainerSummary(id="ffffffffffffcccc", name="z", image="i", state="running"),
    ]
    await reg.start()
    assert reg.container("b", "ffffffffffff").name == "z"
    assert reg.container("b", "abcdef123456") is None  # ambiguous
    assert reg.container("b", "abcdef12345") is None  # too short
    assert reg.container("b", "abcdef123456aaaa").name == "x"
