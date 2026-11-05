"""The host registry: each host's connection, reachability and latest snapshot."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from bleep.hosts.config import HostConfig, ServiceOverrides
from bleep.hosts.discovery import AmbiguousOverride, group_services
from bleep.hosts.engine import Engine, EngineUnavailable, connect_engine
from bleep.hosts.shell import HostShell, engine_basics, host_basics
from bleep.hosts.ssh import KeysFor, Resolve, SshSession
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.services import HostState, Service

log = logging.getLogger(__name__)

BASICS_EVERY_S = 60  # the host probe cadence
BASICS_RETRY_S = 10  # after a failed read

Connector = Callable[[HostConfig], Awaitable[tuple[SshSession | None, Engine]]]


async def connect_host(
    host: HostConfig, keys: KeysFor | None = None, resolve: Resolve | None = None
) -> tuple[SshSession | None, Engine]:
    """The real connector: open the ssh session first (ssh hosts), then build the engine.
    `keys` reads a dashboard host's keys from the secret file; `resolve` looks up its address."""
    session = None
    if host.kind == "ssh":
        session = SshSession(host, keys=keys, resolve=resolve)
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
    logged: set[str] = field(default_factory=set)
    basics_at: datetime | None = None
    basics_wait: float = BASICS_EVERY_S
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
        for entry in list(self._entries.values()):
            async with entry.lock:  # waits for an in-flight reconnect, which closes what it opened
                await _close(entry)

    def add(self, config: HostConfig) -> None:
        """Watch a host added on the dashboard: unreachable until the watcher's first tick
        connects it."""
        if config.id in self._entries:
            raise ValueError(f"host {config.id} is already in the registry")
        self._entries[config.id] = _Entry(HostState(config=config, reachable=False, last_seen=None))

    async def remove(self, host_id: str) -> None:
        """Close the host's connection and forget it; a host not here is a no-op."""
        entry = self._entries.get(host_id)
        if entry is None:
            return
        async with entry.lock:
            self._entries.pop(host_id, None)
            await _close(entry)

    async def replace(self, config: HostConfig, *, reconnect: bool) -> None:
        """Put a dashboard host's changed connection or name in force, keeping its rules and
        services. With `reconnect` the old connection is closed and the last listing dropped (the
        address may name another machine now); the watcher's next tick connects again."""
        entry = self._entries.get(config.id)
        if entry is None:
            return
        async with entry.lock:
            current = entry.state.config
            config = config.model_copy(
                update={
                    "rules": current.rules,
                    "services": current.services,
                    "may_sleep": current.may_sleep,
                }
            )
            if not reconnect:
                self._mark(entry, config=config)
                return
            await _close(entry)
            entry.session = entry.engine = None
            entry.containers = entry.services = None
            entry.basics_at = None
            entry.state = HostState(
                config=config, reachable=False, last_seen=None, error="the connection changed"
            )

    async def refresh(self, host_id: str) -> None:
        """List the host's containers. On a lost connection keep the last snapshot."""
        entry = self._entries.get(host_id)
        if entry is None:  # removed on the dashboard meanwhile
            return
        async with entry.lock:
            if self._entries.get(host_id) is entry:
                await self._refresh(host_id, entry)

    async def reconnect(self, host_id: str) -> bool:
        """Re-open the ssh session, then the engine, then refresh. Never raises.

        Returns whether the host is reachable afterwards; a failure is recorded on its state.
        """
        entry = self._entries.get(host_id)
        if entry is None:
            return False
        async with entry.lock:
            if self._closed or self._entries.get(host_id) is not entry:
                return False
            try:
                if entry.engine is None:
                    session, engine = await self._connector(entry.state.config)
                    if self._closed:  # close() ran while we were connecting
                        await engine.close()
                        if session is not None:
                            await session.close()
                        return False
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

    async def refresh_all(self) -> None:
        """Refresh every reachable host and try to reconnect the others, at once. Never raises."""

        async def one(host_id: str, entry: _Entry) -> None:
            try:
                if entry.state.reachable:
                    await self.refresh(host_id)
                else:
                    await self.reconnect(host_id)
            except Exception:  # one host must not stop the others or the loop
                log.exception("refresh of host %s failed", host_id)

        await asyncio.gather(*(one(i, e) for i, e in list(self._entries.items())))

    def hosts(self) -> list[HostState]:
        return [e.state for e in self._entries.values()]

    def services(self, host_id: str | None = None) -> list[Service]:
        if host_id is None:
            entries = list(self._entries.values())
        else:
            entries = [self._entries[host_id]] if host_id in self._entries else []
        return [s for e in entries for s in (e.services or [])]

    def service(self, service_id: str) -> Service | None:
        host_id = service_id.split("/", 1)[0]
        if host_id not in self._entries:
            return None
        return next((s for s in self.services(host_id) if s.id == service_id), None)

    def snapshot(self, host_id: str) -> list[ContainerSummary] | None:
        """Every container of the last successful listing (Bleep's own too); None if none."""
        entry = self._entries.get(host_id)
        if entry is None or entry.containers is None:
            return None
        return list(entry.containers)

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

    def override(
        self,
        host_id: str,
        rules: list[str],
        services: dict[str, ServiceOverrides],
        may_sleep: bool = False,
    ) -> None:
        """Put the host's rules, service overrides and `may_sleep` in force, the hosts file's
        merged with the settings store's; the connection stays as it is. The services are regrouped
        from the last listing at once, so the URL checks, the plan check and Gather all read the
        new values."""
        entry = self._entries.get(host_id)
        if entry is None:
            return
        update = {"rules": rules, "services": services, "may_sleep": may_sleep}
        config = entry.state.config.model_copy(update=update)
        self._mark(entry, config=config)
        if entry.containers is not None:
            error = self._group(host_id, entry)
            if entry.state.reachable:  # an unreachable host's error is its connection failure
                self._mark(entry, error=error)

    def shell(self, host_id: str) -> HostShell | None:
        """The host's allowlisted shell: its ssh session, or None for a socket host."""
        entry = self._entries.get(host_id)
        return entry.session if entry is not None else None

    def engine(self, host_id: str) -> Engine:
        entry = self._entries.get(host_id)
        if entry is None:
            raise EngineUnavailable(host_id, "no such host")
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
        error = self._group(host_id, entry)
        self._mark(entry, reachable=True, last_seen=self._clock(), error=error)
        await self._refresh_basics(entry)  # after the mark: a slow read never delays reachability

    def _group(self, host_id: str, entry: _Entry) -> str | None:
        """Group the last listing into services; the configuration problem, if there is one."""
        containers = entry.containers or []
        try:
            entry.services = group_services(host_id, containers, entry.state.config.services)
        except AmbiguousOverride as problem:
            # A config problem, not a host problem: stay reachable, keep the previous services
            # (so protected containers stay protected) and show the text on the host.
            error = str(problem)
            if entry.services is None:
                entry.services = group_services(host_id, containers, {})
            if error not in entry.logged:
                entry.logged.add(error)
                log.warning("%s", error)
            return error
        return None

    async def _refresh_basics(self, entry: _Entry) -> None:
        """Read disk, memory and load at most every BASICS_EVERY_S; a failure keeps the old ones."""
        now = self._clock()
        if (
            entry.basics_at is not None
            and (now - entry.basics_at).total_seconds() < entry.basics_wait
        ):
            return
        entry.basics_at = now
        entry.basics_wait = BASICS_RETRY_S  # until a read succeeds
        try:
            if entry.session is not None:
                basics = await host_basics(entry.session)
            elif entry.state.config.kind == "socket" and entry.engine is not None:
                basics = engine_basics(await entry.engine.info())
            else:
                return
        except Exception as error:  # basics are a nicety: never make the host look down
            log.warning("%s: host basics unavailable: %s", entry.state.config.id, _describe(error))
            return
        entry.basics_wait = BASICS_EVERY_S
        self._mark(entry, basics=basics)

    def _mark(self, entry: _Entry, **changes: object) -> None:
        entry.state = replace(entry.state, **changes)


async def _close(entry: _Entry) -> None:
    if entry.engine is not None:
        await entry.engine.close()
    if entry.session is not None:
        await entry.session.close()


def _describe(error: Exception) -> str:
    return str(error) or type(error).__name__
