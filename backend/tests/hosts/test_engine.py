import asyncio
import queue
import time
from datetime import UTC, datetime, timedelta

import docker.errors
import pytest
import requests

from bleep.hosts.config import HostConfig
from bleep.hosts.engine import (
    ContainerNotFound,
    DockerSdkEngine,
    Engine,
    EngineError,
    EngineUnavailable,
    connect_engine,
)
from bleep.hosts.ssh import SshSession, SshUnavailable
from bleep.schemas.docker import ContainerSummary, DockerEvent
from fakes import FakeEngine, FakeSshConnect, ssh_host

T0 = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)


def inspect_dict(name="shop-web-1", cid="c1", **state) -> dict:
    return {
        "Id": cid,
        "Name": f"/{name}",
        "RestartCount": 2,
        "Config": {
            "Image": "shop:latest",
            "Labels": {"com.docker.compose.project": "shop"},
            "Env": ["PATH=/usr/bin", "DB_PASSWORD=s3cret"],
        },
        "State": {
            "Status": "exited",
            "ExitCode": 137,
            "OOMKilled": True,
            "Error": "",
            "StartedAt": "2026-11-02T09:00:00.123456789Z",
            "FinishedAt": "2026-11-02T09:30:00Z",
            "Health": {"Status": "unhealthy"},
        }
        | state,
        "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}, "Memory": 536870912},
        "NetworkSettings": {
            "Ports": {
                "8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8000"}],
                "9000/tcp": None,
            }
        },
        "Mounts": [{"Type": "volume", "Source": "/var/lib/x", "Destination": "/data", "RW": False}],
    }


def list_row(cid="c1", name="shop-web-1", state="exited", status="Exited (137) 5 minutes ago"):
    return {"Id": cid, "Names": [f"/{name}"], "State": state, "Status": status, "Labels": {}}


def raw_event(action="die", name="shop-web-1", nano=1793613600_500000000, type_="container"):
    return {
        "Type": type_,
        "Action": action,
        "Actor": {"ID": "c1", "Attributes": {"name": name, "exitCode": "137"}},
        "time": nano // 1_000_000_000,
        "timeNano": nano,
    }


class FakeEventStream:
    """Blocks in __next__ like the SDK's CancellableStream; close() and drop() end it silently."""

    _END = object()

    def __init__(self) -> None:
        self._items: queue.Queue = queue.Queue()
        self.closed = False

    def push(self, item) -> None:
        self._items.put(item)

    def drop(self) -> None:
        self._items.put(self._END)

    def fail(self, error: BaseException) -> None:
        self._items.put(error)

    def close(self) -> None:
        self.closed = True
        self._items.put(self._END)

    def __iter__(self):
        return self

    def __next__(self):
        item = self._items.get(timeout=5)  # safety net so a broken test cannot hang forever
        if item is self._END:
            raise StopIteration
        if isinstance(item, BaseException):
            raise item
        return item


class FakeApi:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.inspects: dict[str, dict] = {}
        self.inspect_calls: list[str] = []
        self.calls: list[tuple] = []
        self.log_bytes = b""
        self.stream = FakeEventStream()
        self.past_events: list[dict] = []
        self.error: BaseException | None = None

    def _maybe_fail(self) -> None:
        if self.error is not None:
            raise self.error

    def ping(self) -> bool:
        self._maybe_fail()
        self.calls.append(("ping",))
        return True

    def containers(self, all=False, **kwargs):
        self._maybe_fail()
        self.calls.append(("containers", all))
        return list(self.rows)

    def inspect_container(self, container):
        self._maybe_fail()
        self.inspect_calls.append(container)
        if container not in self.inspects:
            raise docker.errors.NotFound(f"No such container: {container}")
        return self.inspects[container]

    def logs(self, container, **kwargs):
        self._maybe_fail()
        self.calls.append(("logs", container, kwargs))
        return self.log_bytes

    def stats(self, container, stream=True, **kwargs):
        self._maybe_fail()
        self.calls.append(("stats", container, stream))
        return {
            "cpu_stats": {
                "cpu_usage": {"total_usage": 400},
                "system_cpu_usage": 2000,
                "online_cpus": 2,
            },
            "precpu_stats": {"cpu_usage": {"total_usage": 200}, "system_cpu_usage": 1000},
            "memory_stats": {"usage": 300, "limit": 1000, "stats": {"inactive_file": 100}},
            "networks": {"eth0": {"rx_bytes": 10, "tx_bytes": 20}},
            "blkio_stats": {
                "io_service_bytes_recursive": [
                    {"op": "read", "value": 5},
                    {"op": "write", "value": 7},
                ]
            },
            "pids_stats": {"current": 4},
        }

    def events(self, since=None, until=None, filters=None, decode=None):
        self._maybe_fail()
        self.calls.append(("events", since, until, filters, decode))
        if until is not None:
            return iter(list(self.past_events))
        return self.stream

    def info(self):
        self._maybe_fail()
        return {
            "Name": "m1",
            "ServerVersion": "29.9.2",
            "OperatingSystem": "Docker Desktop",
            "OSType": "linux",
            "KernelVersion": "7.0.14-linuxkit",
            "Architecture": "aarch64",
            "NCPU": 8,
            "MemTotal": 8_000_000_000,
            "Containers": 3,
            "ContainersRunning": 1,
        }

    def restart(self, container, timeout=None):
        self._maybe_fail()
        self.calls.append(("restart", container, timeout))

    def stop(self, container, timeout=None):
        self._maybe_fail()
        self.calls.append(("stop", container, timeout))

    def start(self, container):
        self._maybe_fail()
        self.calls.append(("start", container))


class FakeDockerClient:
    def __init__(self, api: FakeApi, base_url: str) -> None:
        self.api = api
        self.base_url = base_url
        self.closed = False

    def close(self) -> None:
        self.closed = True


class ClientFactory:
    def __init__(self, api: FakeApi) -> None:
        self.api = api
        self.clients: list[FakeDockerClient] = []
        self.error: BaseException | None = None

    def __call__(self, base_url: str, **kwargs) -> FakeDockerClient:
        if self.error is not None:
            raise self.error
        client = FakeDockerClient(self.api, base_url)
        self.clients.append(client)
        return client


@pytest.fixture
def api() -> FakeApi:
    return FakeApi()


@pytest.fixture
def factory(api) -> ClientFactory:
    return ClientFactory(api)


@pytest.fixture
async def engine(factory):
    eng = DockerSdkEngine("h1", "unix:///tmp/docker.sock", client_factory=factory)
    yield eng
    await eng.close()


async def wait_until(predicate, seconds=2.0) -> None:
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.005)


# -- mapping ---------------------------------------------------------------------------------


async def test_inspect_maps_state_health_oom_and_exit_code(api, engine):
    api.inspects["shop-web-1"] = inspect_dict()
    detail = await engine.inspect("shop-web-1")
    s = detail.summary
    assert (s.id, s.name, s.image, s.state) == ("c1", "shop-web-1", "shop:latest", "exited")
    assert s.health == "unhealthy"
    assert s.exit_code == 137
    assert s.restart_policy == "unless-stopped"
    assert s.labels == {"com.docker.compose.project": "shop"}
    assert s.started_at == datetime(2026, 11, 2, 9, 0, 0, 123456, tzinfo=UTC)
    assert s.finished_at == datetime(2026, 11, 2, 9, 30, tzinfo=UTC)
    assert detail.oom_killed is True
    assert detail.restart_count == 2
    assert detail.mem_limit == 536870912
    assert detail.error is None
    assert detail.env == ["PATH=/usr/bin", "DB_PASSWORD=s3cret"]  # raw; redaction is the tools' job
    assert [(p.container_port, p.host_port) for p in detail.ports] == [
        ("8000/tcp", "8000"),
        ("9000/tcp", None),
    ]
    (mount,) = detail.mounts
    assert (mount.destination, mount.read_only) == ("/data", True)


async def test_inspect_of_a_running_container_without_healthcheck(api, engine):
    raw = inspect_dict(Status="running", ExitCode=0, OOMKilled=False, Error="")
    del raw["State"]["Health"]
    raw["State"]["FinishedAt"] = "0001-01-01T00:00:00Z"
    raw["HostConfig"]["Memory"] = 0
    api.inspects["c1"] = raw
    detail = await engine.inspect("c1")
    assert detail.summary.health == "none"
    assert detail.summary.exit_code is None
    assert detail.summary.finished_at is None
    assert detail.mem_limit is None


async def test_unknown_container_is_container_not_found(engine):
    with pytest.raises(ContainerNotFound) as caught:
        await engine.inspect("ghost")
    assert caught.value.host_id == "h1"


async def test_logs_decode_bytes_with_replacement(api, engine):
    api.log_bytes = b"ok \xff\xfe line\n"
    text = await engine.logs("c1", tail=50, since=T0)
    assert text == "ok �� line\n"
    (_, container, kwargs) = api.calls[-1]
    assert container == "c1" and kwargs["tail"] == 50
    assert kwargs["since"] == T0.timestamp()


async def test_stats_compute_cpu_and_memory(engine):
    stats = await engine.stats("c1")
    assert stats.cpu_percent == pytest.approx(40.0)
    assert (stats.mem_usage, stats.mem_limit) == (200, 1000)
    assert stats.mem_percent == pytest.approx(20.0)
    assert (stats.net_rx, stats.net_tx, stats.block_read, stats.block_write) == (10, 20, 5, 7)
    assert stats.pids == 4


async def test_info_maps_engine_facts(engine):
    info = await engine.info()
    assert (info.server_version, info.cpus, info.mem_total) == ("29.9.2", 8, 8_000_000_000)
    assert info.operating_system == "Docker Desktop"


async def test_act_calls_reach_the_api(api, engine):
    await engine.restart("c1")
    await engine.stop("c1")
    await engine.start("c1")
    assert [c[:2] for c in api.calls[-3:]] == [("restart", "c1"), ("stop", "c1"), ("start", "c1")]


# -- containers(): sparse list plus targeted inspects ----------------------------------------


async def test_containers_use_the_sparse_list_and_inspect_only_what_changed(api, engine):
    api.rows = [list_row("c1", "a"), list_row("c2", "b", "running", "Up 2 hours (healthy)")]
    api.inspects["c1"] = inspect_dict("a", "c1")
    api.inspects["c2"] = inspect_dict("b", "c2", Status="running", Health={"Status": "healthy"})

    first = await engine.containers()
    assert [(c.name, c.state, c.health) for c in first] == [
        ("a", "exited", "unhealthy"),
        ("b", "running", "healthy"),
    ]
    assert all(isinstance(c, ContainerSummary) for c in first)
    assert ("containers", True) in api.calls
    assert sorted(api.inspect_calls) == ["c1", "c2"]

    api.inspect_calls.clear()
    await engine.containers()
    assert api.inspect_calls == []

    api.rows[1] = list_row("c2", "b", "running", "Up 3 seconds (health: starting)")
    api.inspects["c2"] = inspect_dict("b", "c2", Status="running", Health={"Status": "starting"})
    third = await engine.containers()
    assert api.inspect_calls == ["c2"]
    assert third[1].health == "starting"


async def test_containers_skip_one_removed_between_list_and_inspect(api, engine):
    api.rows = [list_row("c1", "a"), list_row("gone", "b")]
    api.inspects["c1"] = inspect_dict("a", "c1")
    assert [c.name for c in await engine.containers()] == ["a"]


# -- connection errors -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        requests.exceptions.ConnectionError(
            "Connection aborted.", FileNotFoundError(2, "No such file or directory")
        ),
        requests.exceptions.ReadTimeout("read timed out"),
        ConnectionRefusedError(61, "refused"),
    ],
)
async def test_connection_errors_are_engine_unavailable(api, engine, error):
    api.error = error
    for call in (engine.containers(), engine.info(), engine.restart("c1"), engine.logs("c1")):
        with pytest.raises(EngineUnavailable) as caught:
            await call
        assert caught.value.host_id == "h1"
        assert caught.value.cause is error


async def test_client_that_cannot_reach_the_engine_is_engine_unavailable(factory, engine):
    factory.error = docker.errors.DockerException("Error while fetching server API version")
    with pytest.raises(EngineUnavailable):
        await engine.containers()


async def test_api_refusal_is_an_engine_error_not_a_drop(api, engine):
    api.error = docker.errors.APIError("403 Forbidden")
    with pytest.raises(EngineError) as caught:
        await engine.restart("c1")
    assert not isinstance(caught.value, EngineUnavailable)


async def test_reconnect_rebuilds_the_client_against_the_current_url(api, factory):
    urls = iter(["unix:///tmp/a/docker.sock", "unix:///tmp/b/docker.sock"])
    current = {"url": next(urls)}
    eng = DockerSdkEngine("h1", lambda: current["url"], client_factory=factory)
    await eng.info()
    current["url"] = next(urls)
    await eng.reconnect()
    await eng.info()
    assert [c.base_url for c in factory.clients] == [
        "unix:///tmp/a/docker.sock",
        "unix:///tmp/b/docker.sock",
    ]
    assert factory.clients[0].closed
    await eng.close()


async def test_reconnect_raises_while_the_engine_is_still_down(api, engine):
    await engine.info()
    api.error = requests.exceptions.ConnectionError("still down")
    with pytest.raises(EngineUnavailable):
        await engine.reconnect()


# -- events ----------------------------------------------------------------------------------


async def test_events_in_a_window_are_parsed_and_exec_noise_dropped(api, engine):
    api.past_events = [
        raw_event("die"),
        raw_event("exec_create: /bin/sh -c curl -H 'Authorization: x'"),
        raw_event("health_status: unhealthy"),
    ]
    events = await engine.events(T0, T0 + timedelta(minutes=5))
    assert [e.action for e in events] == ["die", "health_status: unhealthy"]
    first = events[0]
    assert first == DockerEvent(
        time=datetime.fromtimestamp(1793613600.5, UTC),
        type="container",
        action="die",
        container_id="c1",
        container_name="shop-web-1",
        attributes={"name": "shop-web-1", "exitCode": "137"},
    )
    (_, since, until, filters, decode) = next(c for c in api.calls if c[0] == "events")
    assert since == f"{int(T0.timestamp())}.000000000"
    assert until == f"{int(T0.timestamp()) + 300}.000000000"
    assert filters["type"] == ["container"] and "die" in filters["event"]
    assert decode is True


async def test_stream_events_yields_parsed_events(api, engine):
    api.stream.push(raw_event("start"))
    api.stream.push(raw_event("exec_start: sh -c true"))
    api.stream.push(raw_event("die"))
    seen = []
    async for event in engine.stream_events():
        seen.append(event.action)
        if len(seen) == 2:
            break
    assert seen == ["start", "die"]


async def test_silent_end_of_stream_is_a_drop(api, engine):
    api.stream.push(raw_event("start"))
    api.stream.push(raw_event("die"))
    api.stream.drop()
    seen = []
    with pytest.raises(EngineUnavailable) as caught:
        async for event in engine.stream_events():
            seen.append(event.action)
    assert seen == ["start", "die"]
    assert caught.value.host_id == "h1"


async def test_stream_raising_is_a_drop(api, engine):
    api.stream.fail(requests.exceptions.ConnectionError("reset"))
    with pytest.raises(EngineUnavailable):
        async for _ in engine.stream_events():
            pass


async def test_stop_events_ends_the_stream_normally(api, engine):
    seen = []

    async def consume():
        async for event in engine.stream_events():
            seen.append(event)

    task = asyncio.create_task(consume())
    api.stream.push(raw_event("start"))
    await wait_until(lambda: seen)
    await engine.stop_events()
    await asyncio.wait_for(task, 2)  # returns, does not raise
    assert api.stream.closed


async def test_close_ends_the_stream_normally(api, engine):
    task = asyncio.create_task(anext(engine.stream_events(), None))
    await wait_until(lambda: any(c[0] == "events" for c in api.calls))
    await engine.close()
    assert await asyncio.wait_for(task, 2) is None


async def test_stream_end_after_a_deliberate_ssh_close_is_not_a_drop(api, factory):
    stopping = {"value": False}
    eng = DockerSdkEngine(
        "h1", "unix:///tmp/x.sock", client_factory=factory, stopping=lambda: stopping["value"]
    )
    task = asyncio.create_task(anext(eng.stream_events(), None))
    await wait_until(lambda: any(c[0] == "events" for c in api.calls))
    stopping["value"] = True
    api.stream.drop()  # what closing the SSH connection does to the iterator
    assert await asyncio.wait_for(task, 2) is None
    await eng.close()


async def test_stream_events_does_not_block_the_event_loop(api, engine):
    seen = []

    async def consume():
        async for event in engine.stream_events():
            seen.append(event)

    task = asyncio.create_task(consume())
    start = time.monotonic()
    ticks = 0
    for _ in range(20):
        await asyncio.sleep(0.005)
        ticks += 1
    # the reader thread is parked in a blocking next() the whole time; the loop must not be
    assert ticks == 20 and time.monotonic() - start < 1.0
    assert not task.done()
    api.stream.push(raw_event("die"))
    await wait_until(lambda: seen)
    await engine.stop_events()
    await task


# -- connect_engine --------------------------------------------------------------------------


async def test_connect_engine_socket_host_uses_the_endpoint(factory):
    host = HostConfig(id="demo", name="Demo", kind="socket", endpoint="tcp://socket-proxy:2375")
    eng = connect_engine(host, None, client_factory=factory)
    await eng.info()
    assert factory.clients[0].base_url == "tcp://socket-proxy:2375"
    assert eng.host_id == "demo"
    await eng.close()


async def test_connect_engine_ssh_host_uses_the_forwarded_socket(factory):
    connect = FakeSshConnect()
    ssh = SshSession(ssh_host(), connect=connect)
    await ssh.open()
    eng = connect_engine(ssh.host, ssh, client_factory=factory)
    await eng.info()
    assert factory.clients[0].base_url == f"unix://{ssh.forwarded_socket}"

    connect.connections[0].lose()
    with pytest.raises(EngineUnavailable):
        await eng.reconnect()  # the session is still down: no socket to talk to
    await ssh.ensure_open()
    await eng.reconnect()
    assert factory.clients[-1].base_url == f"unix://{ssh.forwarded_socket}"
    await eng.close()
    await ssh.close()


async def test_connect_engine_ssh_host_needs_an_open_session(factory):
    host = ssh_host()
    with pytest.raises(ValueError):
        connect_engine(host, None, client_factory=factory)
    with pytest.raises(SshUnavailable):
        connect_engine(host, SshSession(host, connect=FakeSshConnect()), client_factory=factory)


# -- FakeEngine ------------------------------------------------------------------------------


def box(name="web-1", state="running") -> ContainerSummary:
    return ContainerSummary(id=f"id-{name}", name=name, image="img", state=state)


async def test_fake_engine_is_an_engine_and_records_acts():
    clock = iter([T0, T0 + timedelta(seconds=5)])
    fake = FakeEngine("h1", [box()], clock=lambda: next(clock))
    assert isinstance(fake, Engine)
    await fake.stop("web-1")
    await fake.start("id-web-1")
    assert [(a.action, a.container, a.at) for a in fake.acts] == [
        ("stop", "web-1", T0),
        ("start", "web-1", T0 + timedelta(seconds=5)),
    ]
    (c,) = await fake.containers()
    assert c.state == "running" and c.started_at == T0 + timedelta(seconds=5)
    with pytest.raises(ContainerNotFound):
        await fake.restart("ghost")


async def test_fake_engine_stream_and_drop():
    fake = FakeEngine("h1", [box()])
    seen = []

    async def consume():
        async for event in fake.stream_events():
            seen.append(event.action)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    fake.emit(DockerEvent(time=T0, type="container", action="die", container_name="web-1"))
    await wait_until(lambda: seen)
    fake.drop()
    with pytest.raises(EngineUnavailable):
        await task
    with pytest.raises(EngineUnavailable):
        await fake.containers()
    fake.restore()
    await fake.reconnect()
    assert [e.action for e in await fake.events(T0, T0 + timedelta(seconds=1))] == ["die"]
