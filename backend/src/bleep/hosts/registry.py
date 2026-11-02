"""The host registry: each host's connection, reachability and latest snapshot."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from bleep.hosts.config import HostConfig
from bleep.hosts.discovery import group_services
from bleep.hosts.engine import Engine, EngineUnavailable, connect_engine
from bleep.hosts.ssh import SshSession
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.services import HostState, Service

Connector = Callable[[HostConfig], Awaitable[tuple[SshSession | None, Engine]]]


async def connect_host(host: HostConfig) -> tuple[SshSession | None, Engine]:
    """The real connector: open the ssh session first (ssh hosts), then build the engine."""
    session = None
    if host.kind == "ssh":
        session = SshSession(host)
        await session.open()
    try:
        return session, connect_engine(host, session)
    except BaseException:
        if session is not None:
            await session.close()
        raise


@dataclass
class _Entry:
    state: HostState
    session: SshSession | None = None
    engine: Engine | None = None
    containers: list[ContainerSummary] | None = None
    services: list[Service] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Registry:
    def __init__(
        self,
        hosts: list[HostConfig],
        connector: Connector = connect_host,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._connector = connector
        self._clock = clock
        self._closed = False
        self._entries = {
            h.id: _Entry(HostState(config=h, reachable=False, last_seen=None)) for h in hosts
        }

    async def start(self) -> None:
        """Connect every host at once. A host that fails stays, unreachable; nothing is raised."""
        await asyncio.gather(*(self.reconnect(i) for i in self._entries))

    async def close(self) -> None:
        self._closed = True
        for entry in self._entries.values():
            if entry.engine is not None:
                await entry.engine.close()
            if entry.session is not None:
                await entry.session.close()

    async def refresh(self, host_id: str) -> None:
        """List the host's containers. On a lost connection keep the last snapshot."""
        entry = self._entries[host_id]
        async with entry.lock:
            await self._refresh(host_id, entry)

    async def reconnect(self, host_id: str) -> bool:
        """Re-open the ssh session, then the engine, then refresh. Never raises.

        Returns whether the host is reachable afterwards; a failure is recorded on its state.
        """
        entry = self._entries[host_id]
        async with entry.lock:
            if self._closed:
                return False
            try:
                if entry.engine is None:
                    session, engine = await self._connector(entry.state.config)
                    entry.session, entry.engine = session, engine
                else:
                    if entry.session is not None:
                        await entry.session.ensure_open()
                    await entry.engine.reconnect()
            except Exception as error:  # the watcher must never see a connector failure
                self._mark(entry, reachable=False, error=_describe(error))
                return False
            await self._refresh(host_id, entry)
            return entry.state.reachable

    def hosts(self) -> list[HostState]:
        return [e.state for e in self._entries.values()]

    def services(self, host_id: str | None = None) -> list[Service]:
        entries = [self._entries[host_id]] if host_id else list(self._entries.values())
        return [s for e in entries for s in (e.services or [])]

    def service(self, service_id: str) -> Service | None:
        host_id = service_id.split("/", 1)[0]
        if host_id not in self._entries:
            return None
        return next((s for s in self.services(host_id) if s.id == service_id), None)

    def container(self, host_id: str, name_or_id: str) -> ContainerSummary | None:
        entry = self._entries.get(host_id)
        if entry is None:
            return None
        for c in entry.containers or []:
            if name_or_id in (c.id, c.name):
                return c
        if len(name_or_id) >= 12:
            prefixed = [c for c in (entry.containers or []) if c.id.startswith(name_or_id)]
            return prefixed[0] if len(prefixed) == 1 else None
        return None

    def engine(self, host_id: str) -> Engine:
        entry = self._entries[host_id]
        if entry.engine is None:
            raise EngineUnavailable(host_id, entry.state.error or "not connected")
        return entry.engine

    async def _refresh(self, host_id: str, entry: _Entry) -> None:
        if entry.engine is None or self._closed:
            return
        try:
            containers = await entry.engine.containers()
        except EngineUnavailable as error:
            self._mark(entry, reachable=False, error=str(error))
            return
        entry.containers = containers
        entry.services = group_services(host_id, containers, entry.state.config.services)
        self._mark(entry, reachable=True, last_seen=self._clock(), error=None)

    def _mark(self, entry: _Entry, **changes: object) -> None:
        entry.state = replace(entry.state, **changes)


def _describe(error: Exception) -> str:
    return str(error) or type(error).__name__
