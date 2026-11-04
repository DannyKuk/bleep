import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from bleep.hosts.config import ServiceOverrides, UrlCheckConfig
from bleep.hosts.engine import EngineUnavailable
from bleep.hosts.registry import Registry
from bleep.schemas.docker import ContainerSummary
from fakes import FakeEngine, socket_host, ssh_host

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
        await asyncio.sleep(0)  # let a concurrent caller interleave
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


def ambiguous_registry():
    host = ssh_host(id="a", name="A", services={"app": ServiceOverrides(repo="me/app")})
    engine = FakeEngine("a", [box("app"), box("app-web-1", "app")])
    return Registry([host], Connector({"a": engine})), engine


async def test_ambiguous_override_is_recorded_without_raising(caplog):
    reg, engine = ambiguous_registry()
    await reg.start()
    state = reg.hosts()[0]
    assert state.reachable and "app" in state.error and "compose:app" in state.error
    assert len(reg.services("a")) == 2
    first = [r for r in caplog.records if "override" in r.getMessage()]
    await reg.refresh("a")
    assert await reg.reconnect("a") is True
    assert reg.hosts()[0].error is not None
    assert len([r for r in caplog.records if "override" in r.getMessage()]) == len(first) == 1


async def test_ambiguity_keeps_the_previous_services():
    reg, engine = ambiguous_registry()
    engine.container_list = [box("app-web-1", "app")]
    await reg.start()
    assert reg.hosts()[0].error is None
    assert reg.service("a/compose:app").repo == "me/app"
    engine.container_list.append(box("app"))
    await reg.refresh("a")
    assert reg.hosts()[0].error is not None
    assert reg.service("a/compose:app").repo == "me/app"
    engine.container_list.pop()
    await reg.refresh("a")
    assert reg.hosts()[0].error is None


async def test_close_during_a_reconnect_closes_what_it_opened():
    reg, engines, connector = make(fail={"b"})
    await reg.start()
    connector.fail.clear()
    reconnecting = asyncio.create_task(reg.reconnect("b"))
    await asyncio.sleep(0)  # the connector is now running
    await reg.close()
    assert await reconnecting is False
    assert engines["b"].closed
    assert reg.hosts()[1].reachable is False


class Clockable:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def basics_registry(shell, host=None, engine=None):
    clock = Clockable()
    host = host or ssh_host(id="a", name="A")
    engine = engine or FakeEngine("a", [box("web")])

    async def connect(h):
        return shell, engine

    return Registry([host], connect, clock=clock), clock


async def test_refresh_fills_basics_from_the_ssh_session():
    from fakes import FakeShell

    reg, _ = basics_registry(FakeShell("linux"))
    await reg.start()
    basics = reg.hosts()[0].basics
    assert basics is not None and basics.os == "Linux" and basics.cpus == 2


async def test_basics_refresh_at_most_every_60_seconds():
    from fakes import FakeShell

    shell = FakeShell("linux")
    reg, clock = basics_registry(shell)
    await reg.start()
    calls = len(shell.calls)
    clock.now = T0 + timedelta(seconds=30)
    await reg.refresh("a")
    assert len(shell.calls) == calls
    clock.now = T0 + timedelta(seconds=61)
    await reg.refresh("a")
    assert len(shell.calls) > calls


async def test_basics_failure_keeps_the_old_ones_and_the_host_reachable():
    from bleep.hosts.ssh import SshUnavailable
    from fakes import FakeShell

    shell = FakeShell("linux")
    reg, clock = basics_registry(shell)
    await reg.start()
    old = reg.hosts()[0].basics
    shell.replies[("uname", "-s")] = SshUnavailable("a", "gone")
    clock.now = T0 + timedelta(seconds=120)
    await reg.refresh("a")
    state = reg.hosts()[0]
    assert state.basics == old and state.reachable


async def test_socket_host_basics_come_from_the_engine():
    from bleep.schemas.docker import EngineInfo

    engine = FakeEngine("a", [box("web")], info=EngineInfo(os_type="linux", cpus=4, mem_total=99))
    reg, _ = basics_registry(None, socket_host(), engine)
    await reg.start()
    basics = reg.hosts()[0].basics
    assert basics is not None and (basics.os, basics.cpus, basics.mem_total) == ("linux", 4, 99)
    assert basics.disks == []


async def test_registry_gives_the_shell_of_an_ssh_host():
    from fakes import FakeShell

    shell = FakeShell("linux")
    reg, _ = basics_registry(shell)
    await reg.start()
    assert reg.shell("a") is shell


async def test_failed_basics_retry_after_10_seconds_a_success_waits_60():
    from bleep.hosts.ssh import SshUnavailable
    from fakes import FakeShell

    shell = FakeShell("linux", {("uname", "-s"): SshUnavailable("a", "gone")})
    reg, clock = basics_registry(shell)
    await reg.start()
    assert reg.hosts()[0].basics is None
    calls = len(shell.calls)
    clock.now = T0 + timedelta(seconds=5)
    await reg.refresh("a")
    assert len(shell.calls) == calls  # too soon
    del shell.replies[("uname", "-s")]
    clock.now = T0 + timedelta(seconds=11)
    await reg.refresh("a")
    assert reg.hosts()[0].basics is not None
    clock.now = T0 + timedelta(seconds=30)  # 19 s after the success
    calls = len(shell.calls)
    await reg.refresh("a")
    assert len(shell.calls) == calls


async def test_reachable_is_marked_before_basics_are_read():
    from fakes import FakeShell

    shell = FakeShell("linux")
    reg, _ = basics_registry(shell)
    seen = []
    original = shell.run

    async def run(argv, timeout=20.0):
        seen.append(reg.hosts()[0].reachable)
        return await original(argv, timeout)

    shell.run = run
    await reg.start()
    assert seen and all(seen)


async def test_override_puts_rules_and_services_in_force_and_keeps_the_connection():
    host = socket_host(id="a", rules=["file rule"])
    web = ContainerSummary(
        id="id-web", name="web-1", image="i", state="running",
        labels={"com.docker.compose.project": "shop"},
    )  # fmt: skip

    async def connect(_host):
        return None, FakeEngine("a", [web])

    registry = Registry([host], connect)
    await registry.start()
    urls = [UrlCheckConfig(url="https://shop.example.test/ready")]
    registry.override("a", ["db rule"], {"compose:shop": ServiceOverrides(urls=urls)})
    state = registry.hosts()[0]
    assert state.config.rules == ["db rule"]
    assert state.config.endpoint == host.endpoint
    assert registry.service("a/compose:shop").urls == urls  # regrouped without a new listing


async def test_override_on_an_unreachable_host_keeps_its_connection_error():
    async def refuse(_host):
        raise OSError("connection refused")

    registry = Registry([socket_host(id="a")], refuse)
    await registry.start()
    registry.override("a", ["db rule"], {})
    state = registry.hosts()[0]
    assert state.config.rules == ["db rule"]
    assert not state.reachable and state.error == "connection refused"


# -- hosts added, changed and removed on the dashboard (#194) -----------------------------------


def dashboard_host(**overrides):
    fields = {"id": "c", "name": "C", "source": "dashboard", "key_file": None}
    return ssh_host(**(fields | {"known_hosts_file": None} | overrides))


async def test_an_added_host_is_unreachable_until_reconnect_then_lists_its_services():
    reg, engines, connector = make()
    await reg.start()
    engines["c"] = FakeEngine("c", [box("blog-web-1", "blog")])
    reg.add(dashboard_host())

    (state,) = [h for h in reg.hosts() if h.config.id == "c"]
    assert not state.reachable and reg.services("c") == []
    assert await reg.reconnect("c")
    assert [s.name for s in reg.services("c")] == ["blog"]
    with pytest.raises(ValueError):
        reg.add(dashboard_host())


async def test_a_removed_host_is_closed_and_every_call_for_it_is_harmless():
    reg, engines, _ = make()
    await reg.start()
    await reg.remove("b")

    assert engines["b"].closed
    assert [h.config.id for h in reg.hosts()] == ["a"]
    assert reg.services("b") == [] and reg.snapshot("b") is None and reg.shell("b") is None
    await reg.refresh("b")
    assert not await reg.reconnect("b")
    with pytest.raises(EngineUnavailable):
        reg.engine("b")
    reg.override("b", ["a rule"], {})
    await reg.remove("b")  # twice is a no-op


async def test_a_reconnect_in_flight_when_the_host_is_removed_leaves_nothing_open():
    engine = FakeEngine("c", [box("blog")])
    release = asyncio.Event()

    async def slow(host):
        await release.wait()
        return None, engine

    reg = Registry([], slow, clock=Clock())
    reg.add(dashboard_host())
    reconnecting = asyncio.create_task(reg.reconnect("c"))
    await asyncio.sleep(0)
    removing = asyncio.create_task(reg.remove("c"))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(reconnecting, removing)

    assert engine.closed  # remove waited for the connect, then closed what it opened
    assert reg.hosts() == []


async def test_a_new_connection_closes_the_old_one_and_drops_the_old_listing():
    reg, engines, _ = make()
    await reg.start()
    reg.override("b", ["be gentle"], {"redis": ServiceOverrides(protected_containers=["redis"])})

    await reg.replace(ssh_host(id="b", name="B", address="new.local"), reconnect=True)
    (state,) = [h for h in reg.hosts() if h.config.id == "b"]
    assert engines["b"].closed
    assert not state.reachable and state.config.address == "new.local"
    assert reg.snapshot("b") is None and reg.services("b") == []
    assert state.config.rules == ["be gentle"]  # the dashboard's rules stay with the host
    assert state.config.services["redis"].protected_containers == ["redis"]


async def test_a_new_name_alone_keeps_the_connection():
    reg, engines, _ = make()
    await reg.start()
    await reg.replace(ssh_host(id="b", name="Bee"), reconnect=False)
    (state,) = [h for h in reg.hosts() if h.config.id == "b"]
    assert state.config.name == "Bee" and state.reachable and not engines["b"].closed
