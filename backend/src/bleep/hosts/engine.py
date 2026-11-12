"""The Docker engine of one host, read and acted on through the docker SDK.

Every SDK call runs in a worker thread (`asyncio.to_thread`): the SDK blocks, and on an ssh host
the socket forward it talks to is served by this event loop, so a call on the loop would wait
for itself (docs/decisions/docker-over-ssh.md).

The drop contract (docs/design/hosts.md):
- A lost connection makes every method raise `EngineUnavailable(host_id, cause)`.
- `stream_events()` raises `EngineUnavailable` when its stream ends without being asked to;
  the SDK ends it silently on a drop. It returns normally only after `stop_events()`, `close()`
  or a deliberate `SshSession.close()`.
- The engine never reconnects by itself. The watcher reconnects with backoff: for an ssh host
  `SshSession.ensure_open()` and then `engine.reconnect()`, which builds a new SDK client
  against the (possibly new) forwarded socket.
"""

import asyncio
import contextlib
import re
import threading
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast, runtime_checkable

import docker
import docker.errors
import urllib3.exceptions

from bleep.hosts.config import HostConfig
from bleep.hosts.ssh import SshSession, SshUnavailable
from bleep.schemas.docker import (
    ContainerDetail,
    ContainerStats,
    ContainerSummary,
    DockerEvent,
    EngineInfo,
    HealthCheck,
    HealthProbe,
    LastLog,
    MountInfo,
    PortBinding,
)

CALL_TIMEOUT_S = 60  # longer than STOP_TIMEOUT_S, so a slow stop is not read as a drop
STOP_TIMEOUT_S = 10
INSPECT_CONCURRENCY = 8
# Events waiting for a slow consumer. Past this the stream ends as unavailable, so the watcher
# reconnects and replays with events(since=...) instead of events being dropped.
EVENT_BACKLOG = 1000
# Humanized ages that can repeat across two polls while the container restarted in between.
_YOUNG = re.compile(r"Less than a second|\b\d+ seconds?\b")

# Filtered on the engine: a Coolify host sends about 3.6 exec_* health-check events a second
# whose action text carries the whole command line (docs/decisions/docker-over-ssh.md).
CONTAINER_ACTIONS = [
    "create",
    "start",
    "restart",
    "die",
    "kill",
    "oom",
    "stop",
    "destroy",
    "health_status",
    "pause",
    "unpause",
]
EVENT_FILTERS = {"type": ["container"], "event": CONTAINER_ACTIONS}

ClientFactory = Callable[..., Any]
Health = Literal["healthy", "unhealthy", "starting", "none"]


class EngineError(Exception):
    """The engine answered but refused or failed the request."""

    def __init__(self, host_id: str, message: str) -> None:
        super().__init__(f"{host_id}: {message}")
        self.host_id = host_id


class EngineUnavailable(EngineError):
    """The engine cannot be reached: the connection is lost, closed or was never made."""

    def __init__(self, host_id: str, cause: BaseException | str) -> None:
        super().__init__(host_id, f"engine unavailable: {str(cause) or type(cause).__name__}")
        self.cause = cause


class ContainerNotFound(EngineError):
    def __init__(self, host_id: str, container: str) -> None:
        super().__init__(host_id, f"no container {container!r} on this host")
        self.container = container


@runtime_checkable
class Engine(Protocol):
    """What Bleep needs from a Docker engine. `container` is a name or an id."""

    host_id: str

    async def containers(self) -> list[ContainerSummary]: ...
    async def inspect(self, container: str) -> ContainerDetail: ...
    async def logs(self, container: str, tail: int = 200, since: datetime | None = None) -> str: ...
    async def stats(self, container: str) -> ContainerStats: ...
    async def events(self, since: datetime, until: datetime) -> list[DockerEvent]: ...
    def stream_events(self) -> AsyncIterator[DockerEvent]: ...
    async def stop_events(self) -> None: ...
    async def info(self) -> EngineInfo: ...
    async def restart(self, container: str) -> None: ...
    async def stop(self, container: str) -> None: ...
    async def start(self, container: str) -> None: ...
    async def reconnect(self) -> None: ...
    async def close(self) -> None: ...


@runtime_checkable
class LogClock(Protocol):
    """An engine that can say when a container last wrote a log line. Separate from `Engine`:
    a fake or scripted engine may lack it, and the log tools then say nothing about it."""

    async def last_log(self, container: str) -> LastLog | None: ...


class _Stream:
    """One open event stream: the SDK's blocking iterator and how it is being ended."""

    def __init__(self, raw: Any) -> None:
        self.raw = raw
        self.stopped = False
        self.backlogged = False

    def close(self) -> None:
        # Shuts the response's socket down; does not wait on the network. Already closed is fine.
        with contextlib.suppress(OSError, AttributeError):
            self.raw.close()


class DockerSdkEngine:
    """An `Engine` over a docker SDK client at `base_url` (a string, or a callable for ssh hosts
    whose forwarded socket changes on every reconnect).

    `stopping` tells a stream's end after a deliberate stop elsewhere (the ssh session's
    `close()`) from a drop.
    """

    def __init__(
        self,
        host_id: str,
        base_url: str | Callable[[], str],
        *,
        client_factory: ClientFactory = docker.DockerClient,
        stopping: Callable[[], bool] = lambda: False,
    ) -> None:
        self.host_id = host_id
        self._base_url = base_url if callable(base_url) else (lambda: base_url)
        self._factory = client_factory
        self._stopping = stopping
        self._client: Any = None
        self._client_lock = asyncio.Lock()
        self._streams: set[_Stream] = set()
        self._closed = False
        self._stop_requests = 0
        # container id -> ((State, Status) of the list row when inspected, the inspect)
        self._inspected: dict[str, tuple[tuple[str, str], dict]] = {}

    async def containers(self) -> list[ContainerSummary]:
        """All containers, running or not.

        The sparse list (one request) lacks restart policy and start time, so a container is
        inspected again when its list row's State or Status text changed since its last inspect.
        The text is humanized ("Up 2 hours"), so rows that can repeat across a restart are always
        inspected: State `restarting` and ages in seconds. An act or a streamed event for the
        container drops its cached inspect too.
        """
        rows = await self._call(lambda c: c.api.containers(all=True))
        keys = {row["Id"]: (row.get("State", ""), row.get("Status", "")) for row in rows}
        stale = [
            cid
            for cid, key in keys.items()
            if _young(*key) or self._inspected.get(cid, ((), {}))[0] != key
        ]
        limit = asyncio.Semaphore(INSPECT_CONCURRENCY)

        async def refresh(cid: str) -> None:
            async with limit:
                try:
                    raw = await self._call(lambda c: c.api.inspect_container(cid), cid)
                except ContainerNotFound:
                    self._inspected.pop(cid, None)  # removed between the list and the inspect
                    return
            self._inspected[cid] = (keys[cid], raw)

        await asyncio.gather(*(refresh(cid) for cid in stale))
        for cid in set(self._inspected) - set(keys):
            del self._inspected[cid]
        return [
            _summary(self._inspected[row["Id"]][1]) for row in rows if row["Id"] in self._inspected
        ]

    async def inspect(self, container: str) -> ContainerDetail:
        raw = await self._call(lambda c: c.api.inspect_container(container), container)
        return _detail(raw)

    async def logs(self, container: str, tail: int = 200, since: datetime | None = None) -> str:
        options: dict[str, Any] = {"stdout": True, "stderr": True, "tail": tail}
        if since is not None:
            options["since"] = _aware(since).timestamp()
        raw = await self._call(lambda c: c.api.logs(container, **options), container)
        return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)

    async def last_log(self, container: str) -> LastLog | None:
        """When the container last wrote a line, and the time on this engine's own clock now, so
        the age does not depend on the clock of the machine Bleep runs on. None when the engine
        cannot say: no clock, or a first log word that is not a timestamp."""
        raw = await self._call(
            lambda c: c.api.logs(container, stdout=True, stderr=True, tail=1, timestamps=True),
            container,
        )
        now = (await self.info()).system_time
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        if now is None:
            return None
        if not text.strip():
            return LastLog(at=None, now=now)
        at = _log_time(text.split(None, 1)[0])
        return None if at is None else LastLog(at=at, now=now)

    async def stats(self, container: str) -> ContainerStats:
        raw = await self._call(lambda c: c.api.stats(container, stream=False), container)
        return _stats(raw)

    async def events(self, since: datetime, until: datetime) -> list[DockerEvent]:
        """Container events in a past window, to replay what a dropped stream missed."""
        until = min(_aware(until), datetime.now(UTC))  # a future `until` would block until then

        def read(client: Any) -> list[dict]:
            raw = client.api.events(
                since=_docker_time(since),
                until=_docker_time(until),
                filters=EVENT_FILTERS,
                decode=True,
            )
            found = list(raw)
            # The SDK ends a stream silently on a drop too; a ping tells a complete window apart.
            client.api.ping()
            return found

        events = [e for e in map(_event, await self._call(read)) if e is not None]
        for event in events:
            self._forget_id(event.container_id)
        return events

    async def stream_events(self) -> AsyncIterator[DockerEvent]:
        """Live container events. Raises `EngineUnavailable` on a drop (see the module doc)."""
        requested = self._stop_requests
        raw = await self._call(lambda c: c.api.events(filters=EVENT_FILTERS, decode=True))
        stream = _Stream(raw)
        self._streams.add(stream)
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

        def offer(item: tuple[str, Any]) -> None:
            """Runs on the loop for each item from the reader thread."""
            if stream.backlogged:
                return
            if item[0] == "event" and queue.qsize() >= EVENT_BACKLOG:
                stream.backlogged = True
                stream.close()  # ends the reader thread
                item = ("backlog", None)
            queue.put_nowait(item)

        try:
            if self._closed or self._stop_requests != requested:
                return  # a stop arrived while the stream was being opened
            # A thread of its own rather than to_thread: it blocks for the life of the stream
            # and would hold one of the default executor's few workers for days.
            threading.Thread(
                target=_pump,
                args=(raw, asyncio.get_running_loop(), offer),
                name=f"events-{self.host_id}",
                daemon=True,
            ).start()
            while True:
                kind, item = await queue.get()
                if kind == "event":
                    if (event := _event(item)) is not None:
                        self._forget_id(event.container_id)
                        yield event
                    continue
                if stream.stopped or self._closed or self._stopping():
                    return
                causes = {
                    "error": item,
                    "end": "event stream ended without a stop",
                    "backlog": f"event backlog of {EVENT_BACKLOG} unread events",
                }
                raise EngineUnavailable(self.host_id, causes[kind])
        finally:
            self._streams.discard(stream)
            stream.close()

    async def stop_events(self) -> None:
        """End every open `stream_events()` normally, also one still being opened."""
        self._stop_requests += 1
        for stream in list(self._streams):
            stream.stopped = True  # before closing, so the end is not read as a drop
            stream.close()

    async def info(self) -> EngineInfo:
        return _info(await self._call(lambda c: c.api.info()))

    async def restart(self, container: str) -> None:
        await self._act(lambda c: c.api.restart(container, timeout=STOP_TIMEOUT_S), container)

    async def stop(self, container: str) -> None:
        await self._act(lambda c: c.api.stop(container, timeout=STOP_TIMEOUT_S), container)

    async def start(self, container: str) -> None:
        await self._act(lambda c: c.api.start(container), container)

    async def reconnect(self) -> None:
        """Drop the client and build a new one against the current base URL, then ping.

        Raises `EngineUnavailable` while the engine is still unreachable.
        """
        async with self._client_lock:
            old, self._client = self._client, None
        if old is not None:
            await asyncio.to_thread(_close_quietly, old)
        await self._call(lambda c: c.api.ping())

    async def close(self) -> None:
        self._closed = True
        await self.stop_events()
        async with self._client_lock:
            old, self._client = self._client, None
        if old is not None:
            await asyncio.to_thread(_close_quietly, old)

    async def _act(self, fn: Callable[[Any], object], container: str) -> None:
        try:
            await self._call(fn, container)
        finally:
            self._forget(container)  # its start time, exit code and restart count changed

    def _forget(self, container: str) -> None:
        """Drop the cached inspect of a container given by name, id or id prefix."""
        for cid, (_, raw) in list(self._inspected.items()):
            if cid.startswith(container) or raw.get("Name", "").lstrip("/") == container:
                del self._inspected[cid]

    def _forget_id(self, container_id: str | None) -> None:
        if container_id is not None:
            self._inspected.pop(container_id, None)

    async def _ensure_client(self) -> Any:
        if self._closed:
            raise EngineUnavailable(self.host_id, "engine is closed")
        async with self._client_lock:
            if self._client is None:
                try:
                    url = self._base_url()
                    # The constructor asks the engine for its API version: a network call.
                    self._client = await asyncio.to_thread(
                        self._factory, base_url=url, timeout=CALL_TIMEOUT_S
                    )
                except Exception as exc:
                    raise self._translate(exc) from exc
            return self._client

    async def _call[T](self, fn: Callable[[Any], T], container: str | None = None) -> T:
        client = await self._ensure_client()
        try:
            return await asyncio.to_thread(fn, client)
        except Exception as exc:
            error = self._translate(exc, container)
            if error is exc:
                raise
            raise error from exc

    def _translate(self, exc: Exception, container: str | None = None) -> Exception:
        match exc:
            case EngineError():
                return exc
            case docker.errors.NotFound() if container is not None:
                return ContainerNotFound(self.host_id, container)
            case docker.errors.APIError():
                return EngineError(self.host_id, f"engine refused: {exc.explanation or exc}")
            case (
                docker.errors.DockerException()
                | SshUnavailable()
                | urllib3.exceptions.HTTPError()
                | OSError()
            ):
                return EngineUnavailable(self.host_id, exc)
            case _:
                return exc


def connect_engine(
    host: HostConfig,
    ssh: SshSession | None = None,
    *,
    client_factory: ClientFactory = docker.DockerClient,
) -> DockerSdkEngine:
    """The engine of `host`: its endpoint for a socket host, the session's forward for ssh.

    An ssh host needs its session opened first. The client is built on first use.
    """
    if host.kind == "socket":
        assert host.endpoint is not None  # HostConfig validates it for socket hosts
        return DockerSdkEngine(host.id, host.endpoint, client_factory=client_factory)
    if ssh is None:
        raise ValueError(f"host {host.id} is an ssh host and needs an SshSession")
    if not ssh.connected:
        raise SshUnavailable(host.id, "open the session before connecting the engine")
    session = ssh
    return DockerSdkEngine(
        host.id,
        lambda: f"unix://{session.forwarded_socket}",
        client_factory=client_factory,
        stopping=lambda: session.stopped,
    )


def _pump(
    raw: Any, loop: asyncio.AbstractEventLoop, offer: Callable[[tuple[str, Any]], None]
) -> None:
    """Reader thread: hand each event to the loop, then how the stream ended."""

    def put(item: tuple[str, Any]) -> bool:
        try:
            loop.call_soon_threadsafe(offer, item)
            return True
        except RuntimeError:
            return False  # the loop is closed; nobody is listening any more

    try:
        for event in raw:
            if not put(("event", event)):
                return
    except Exception as exc:
        put(("error", exc))
    else:
        put(("end", None))


def _close_quietly(client: Any) -> None:
    with contextlib.suppress(OSError):  # the socket under it is already gone
        client.close()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _docker_time(value: datetime) -> str:
    """Seconds.nanoseconds, which the events API takes without losing the sub-second part."""
    micros = round(_aware(value).timestamp() * 1_000_000)
    return f"{micros // 1_000_000}.{(micros % 1_000_000) * 1000:09d}"


def _time(value: str | None) -> datetime | None:
    if not value or value.startswith("0001-01-01"):
        return None
    return datetime.fromisoformat(value)


def _young(state: str, status: str) -> bool:
    """A row whose text may look the same on the next poll although the container restarted."""
    return state == "restarting" or _YOUNG.search(status) is not None


def _health(state: dict) -> Health:
    status = (state.get("Health") or {}).get("Status")
    return cast(Health, status) if status in ("healthy", "unhealthy", "starting") else "none"


def _summary(raw: dict) -> ContainerSummary:
    state = raw.get("State") or {}
    config = raw.get("Config") or {}
    status = state.get("Status", "")
    restart_policy = ((raw.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name", "")
    return ContainerSummary(
        id=raw["Id"],
        name=raw.get("Name", "").lstrip("/"),
        image=config.get("Image", ""),
        state=status,
        health=_health(state),
        labels=config.get("Labels") or {},
        restart_policy=restart_policy or "",
        restart_count=raw.get("RestartCount") or 0,
        exit_code=None if status in ("running", "created") else state.get("ExitCode"),
        started_at=_time(state.get("StartedAt")),
        finished_at=_time(state.get("FinishedAt")),
        created_at=_time(raw.get("Created")),
    )


def _seconds(nanos: int | None) -> float | None:
    """A Docker duration (nanoseconds); 0 or missing means the check uses Docker's default."""
    return nanos / 1_000_000_000 if nanos else None


def _healthcheck(raw: dict) -> HealthCheck | None:
    check = (raw.get("Config") or {}).get("Healthcheck") or {}
    test = [str(part) for part in check.get("Test") or []]
    if not test or test[0] == "NONE" or not test[1:]:
        return None
    return HealthCheck(
        command=test[1:],
        shell=test[0] == "CMD-SHELL",
        interval_s=_seconds(check.get("Interval")),
        timeout_s=_seconds(check.get("Timeout")),
        retries=check.get("Retries") or None,
        start_period_s=_seconds(check.get("StartPeriod")),
    )


def _probes(state: dict) -> list[HealthProbe]:
    return [
        HealthProbe(
            start=_time(entry.get("Start")),
            end=_time(entry.get("End")),
            exit_code=entry.get("ExitCode"),
            output=entry.get("Output") or "",
        )
        for entry in (state.get("Health") or {}).get("Log") or []
    ]


def _detail(raw: dict) -> ContainerDetail:
    state = raw.get("State") or {}
    ports = [
        PortBinding(container_port=port, host_ip=b.get("HostIp"), host_port=b.get("HostPort"))
        for port, bindings in ((raw.get("NetworkSettings") or {}).get("Ports") or {}).items()
        for b in (bindings or [{}])
    ]
    mounts = [
        MountInfo(
            type=m.get("Type", ""),
            source=m.get("Source", ""),
            destination=m.get("Destination", ""),
            read_only=not m.get("RW", True),
        )
        for m in raw.get("Mounts") or []
    ]
    return ContainerDetail(
        summary=_summary(raw),
        env=(raw.get("Config") or {}).get("Env") or [],
        ports=ports,
        mounts=mounts,
        mem_limit=(raw.get("HostConfig") or {}).get("Memory") or None,
        oom_killed=bool(state.get("OOMKilled")),
        restart_count=raw.get("RestartCount", 0),
        error=state.get("Error") or None,
        healthcheck=_healthcheck(raw),
        probes=_probes(state),
    )


def _stats(raw: dict) -> ContainerStats:
    """The `docker stats` arithmetic over one sample and the engine's previous one."""
    cpu, pre = raw.get("cpu_stats") or {}, raw.get("precpu_stats") or {}
    cpu_delta = (cpu.get("cpu_usage") or {}).get("total_usage", 0) - (
        pre.get("cpu_usage") or {}
    ).get("total_usage", 0)
    system_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
    cpus = cpu.get("online_cpus") or len((cpu.get("cpu_usage") or {}).get("percpu_usage") or [1])
    cpu_percent = cpu_delta / system_delta * cpus * 100 if cpu_delta > 0 and system_delta > 0 else 0
    memory = raw.get("memory_stats") or {}
    cache = (memory.get("stats") or {}).get("inactive_file") or (memory.get("stats") or {}).get(
        "total_inactive_file", 0
    )
    usage = max(memory.get("usage", 0) - cache, 0)
    limit = memory.get("limit", 0)
    networks = (raw.get("networks") or {}).values()
    blkio = (raw.get("blkio_stats") or {}).get("io_service_bytes_recursive") or []
    return ContainerStats(
        cpu_percent=cpu_percent,
        mem_usage=usage,
        mem_limit=limit,
        mem_percent=usage / limit * 100 if limit else 0.0,
        net_rx=sum(n.get("rx_bytes", 0) for n in networks),
        net_tx=sum(n.get("tx_bytes", 0) for n in networks),
        block_read=sum(b.get("value", 0) for b in blkio if b.get("op", "").lower() == "read"),
        block_write=sum(b.get("value", 0) for b in blkio if b.get("op", "").lower() == "write"),
        pids=(raw.get("pids_stats") or {}).get("current", 0),
    )


def _event(raw: dict) -> DockerEvent | None:
    """One event; None for exec_* events, whose action text carries a command line."""
    action = raw.get("Action") or raw.get("status") or ""
    if action.startswith("exec_"):
        return None
    kind = raw.get("Type", "")
    actor = raw.get("Actor") or {}
    attributes = {str(k): str(v) for k, v in (actor.get("Attributes") or {}).items()}
    nanos = raw.get("timeNano") or raw.get("time", 0) * 1_000_000_000
    return DockerEvent(
        time=datetime.fromtimestamp(nanos / 1_000_000_000, UTC),
        type=kind,
        action=action,
        container_id=actor.get("ID") if kind == "container" else None,
        container_name=attributes.get("name") if kind == "container" else None,
        attributes=attributes,
    )


def _info(raw: dict) -> EngineInfo:
    return EngineInfo(
        name=raw.get("Name", ""),
        server_version=raw.get("ServerVersion", ""),
        operating_system=raw.get("OperatingSystem", ""),
        os_type=raw.get("OSType", ""),
        kernel_version=raw.get("KernelVersion", ""),
        architecture=raw.get("Architecture", ""),
        cpus=raw.get("NCPU", 0),
        mem_total=raw.get("MemTotal", 0),
        containers=raw.get("Containers", 0),
        containers_running=raw.get("ContainersRunning", 0),
        system_time=_log_time(raw.get("SystemTime", "")),
    )


_LOG_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")


def _log_time(stamp: str) -> datetime | None:
    """A Docker RFC 3339 timestamp, or None when the text is not exactly one."""
    if not _LOG_STAMP.fullmatch(stamp):
        return None
    try:
        return _aware(datetime.fromisoformat(stamp))
    except ValueError:
        return None
