"""The hosts and services as the overview shows them: each with its health, its quiet and its
open incident, judged from what the watcher last saw.

The dashboard's read routes and the conversation (`talk`) read the same views, so a chat answer
never judges a service differently from the page (docs/design/watcher.md).
"""

from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime

from bleep.hosts.registry import Registry
from bleep.hosts.url_check import UrlCheck
from bleep.incidents.quiet import QuietStore
from bleep.incidents.repository import IncidentRepo, utcnow
from bleep.schemas.api import (
    BasicsView,
    ContainerView,
    DiskView,
    HostView,
    QuietView,
    ServiceView,
    WatchView,
)
from bleep.schemas.docker import HostBasics
from bleep.schemas.incidents import Subject
from bleep.schemas.quiet import QuietState
from bleep.schemas.services import HostState, Service
from bleep.watcher.detector import Pending
from bleep.watcher.health import host_health, is_asleep, service_health, url_status
from bleep.watcher.http_checks import UrlCheckStore

Checks = dict[tuple[str, str], tuple[UrlCheck, datetime]]
Watched = dict[str, list[WatchView]]  # by subject key


def quiet_view(state: QuietState | None) -> QuietView | None:
    if state is None:
        return None
    return QuietView.of(state)


def quiet_of(service: Service, active: list[QuietState]) -> QuietState | None:
    """The service's host's state if it has one (the host wins), else its own."""
    own = None
    for state in active:
        if state.scope == "host" and state.target_id == service.host_id:
            return state
        if state.scope == "service" and state.target_id == service.id:
            own = state
    return own


def watch_view(pending: Pending) -> WatchView:
    return WatchView(
        kind=pending.kind,
        since=pending.since,
        opens_after=pending.opens_after,
        url=pending.url,
        container=pending.container,
        failures=pending.failures,
        failures_to_open=pending.failures_to_open,
    )


def watched_by_subject(pending: Iterable[Pending]) -> Watched:
    """The manager's `watching()` as views, by subject key."""
    found: Watched = {}
    for item in pending:
        found.setdefault(item.subject.key, []).append(watch_view(item))
    return found


def service_view(
    service: Service,
    open_incident_id: str | None,
    checks: Checks,
    now: datetime,
    quiet: QuietState | None,
    asleep: bool = False,
    watching: list[WatchView] | None = None,
) -> ServiceView:
    urls = [url_status(u.url, checks.get((service.id, u.url)), now) for u in service.urls]
    health = service_health(service.containers, (u.status for u in urls), service.exits)
    return ServiceView(
        id=service.id,
        host_id=service.host_id,
        name=service.name,
        source=service.source,
        # Its host sleeps: its last listing and its failing URLs say nothing about the service.
        health="asleep" if asleep else health,
        containers=[
            ContainerView(
                id=c.id,
                name=c.name,
                image=c.image,
                state=c.state,
                health=c.health,
                restart_count=c.restart_count,
                exit_code=c.exit_code,
                protected=c.name in service.protected,
            )
            for c in service.containers
        ],
        urls=urls,
        open_incident_id=open_incident_id,
        quiet=quiet_view(quiet),
        watching=[] if asleep else list(watching or []),
    )


def _basics(basics: HostBasics | None) -> BasicsView | None:
    if basics is None:
        return None
    return BasicsView(
        **basics.model_dump(exclude={"disks"}),
        disks=[DiskView(mount=d.mount, used_percent=d.used_percent) for d in basics.disks],
    )


class Overview:
    def __init__(
        self,
        registry: Registry,
        incidents: IncidentRepo,
        quiet: QuietStore,
        url_checks: UrlCheckStore,
        clock: Callable[[], datetime] = utcnow,
        watching: Callable[[], Awaitable[list[Pending]]] | None = None,
    ) -> None:
        """`watching` is the running watcher's `WatcherManager.watching`; without it no view
        says what the watcher waits on."""
        self.registry = registry
        self.incidents = incidents
        self.quiet = quiet
        self.url_checks = url_checks
        self.clock = clock
        self._watching = watching

    async def watched(self) -> Watched:
        return watched_by_subject(await self._watching()) if self._watching else {}

    async def checks(self, host_id: str) -> Checks:
        """Each URL's latest check on the host, by (service id, URL)."""
        return {
            (sid, check.url): (check, at)
            for sid, check, at in await self.url_checks.latest(host_id)
        }

    async def services(
        self,
        host_id: str | None = None,
        active: list[QuietState] | None = None,
        watched: Watched | None = None,
    ) -> list[ServiceView]:
        """Every service, or the host `host_id`'s; none for a host Bleep does not know."""
        if host_id is not None and host_id not in {h.config.id for h in self.registry.hosts()}:
            return []
        active = await self.quiet.active() if active is None else active
        watched = await self.watched() if watched is None else watched
        now = self.clock()
        checks: dict[str, Checks] = {}
        asleep = {h.config.id for h in self.registry.hosts() if is_asleep(h)}
        views = []
        for service in self.registry.services(host_id):
            if service.host_id not in checks:
                checks[service.host_id] = await self.checks(service.host_id)
            subject = Subject(kind="service", host_id=service.host_id, service_id=service.id)
            open_id = await self.incidents.open_incident_id(subject)
            views.append(
                service_view(
                    service,
                    open_id,
                    checks[service.host_id],
                    now,
                    quiet_of(service, active),
                    service.host_id in asleep,
                    watched.get(subject.key),
                )
            )
        return views

    async def hosts(self) -> list[HostView]:
        result = []
        active = await self.quiet.active()
        watched = await self.watched()
        for state in self.registry.hosts():
            result.append(await self._host(state, active, watched))
        return result

    async def _host(self, state: HostState, active: list[QuietState], watched: Watched) -> HostView:
        views = await self.services(state.config.id, active, watched)
        host_key = Subject(kind="host", host_id=state.config.id).key
        unreachable = [w for w in watched.get(host_key, []) if w.kind == "unreachable"]
        host_quiet = next(
            (q for q in active if q.scope == "host" and q.target_id == state.config.id), None
        )
        judged = [v.health for v in views if host_quiet is not None or v.quiet is None]
        return HostView(
            id=state.config.id,
            name=state.config.name,
            kind=state.config.kind,
            reachable=state.reachable,
            last_seen=state.last_seen,
            error=state.error,
            basics=_basics(state.basics),
            service_count=len(views),
            health=host_health(state.reachable, state.last_seen, judged, state.config.may_sleep),
            may_sleep=state.config.may_sleep,
            quiet=quiet_view(host_quiet),
            quiet_service_ids=[v.id for v in views if v.quiet is not None],
            # Only while the registry also calls it unreachable, so the two never disagree.
            watching=unreachable[0] if unreachable and not state.reachable else None,
        )
