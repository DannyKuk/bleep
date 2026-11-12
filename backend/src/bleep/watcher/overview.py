"""The hosts and services as the overview shows them: each with its health, its quiet and its
open incident, judged from what the watcher last saw. A service that shows down but that the
watcher will open no incident for says why (`UnwatchedView`, #478).

The dashboard's read routes and the conversation (`talk`) read the same views, so a chat answer
never judges a service differently from the page (docs/design/watcher.md).
"""

from collections.abc import Awaitable, Callable, Collection, Iterable
from datetime import datetime

from bleep.hosts.discovery import expects_exit
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
    UnwatchedView,
    WatchView,
)
from bleep.schemas.docker import ContainerSummary, HostBasics
from bleep.schemas.incidents import TERMINAL_STATUSES, Incident, Subject
from bleep.schemas.quiet import QuietState
from bleep.schemas.services import HostState, Service
from bleep.watcher.detector import Pending
from bleep.watcher.health import (
    container_health,
    host_health,
    is_asleep,
    service_health,
    url_status,
)
from bleep.watcher.health_store import HealthStore
from bleep.watcher.http_checks import UrlCheckStore
from bleep.watcher.sightings import HostSightings, Sightings

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
    stopped_on_purpose: Collection[str] = (),
) -> ServiceView:
    urls = [url_status(u.url, checks.get((service.id, u.url)), now) for u in service.urls]
    statuses = (u.status for u in urls)
    health = service_health(service.containers, statuses, service.exits, stopped_on_purpose)
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


def is_unwatched(view: ServiceView, host: HostState) -> bool:
    """Down, with nothing the watcher waits on, no incident open, not quiet, on a host Bleep
    reaches: no incident will open for it unless someone asks for a look. The dashboard applies
    the same rule (`unwatchedDownServices` in `lib/mood.ts`), so the reason only refines it."""
    return (
        view.health == "down"
        and not view.watching
        and view.open_incident_id is None
        and view.quiet is None
        and host.reachable
        and not is_asleep(host)
    )


def down_containers(
    service: Service, stopped_on_purpose: Collection[str]
) -> list[ContainerSummary]:
    """The service's containers that show down."""

    def judged(c: ContainerSummary) -> str:
        expected = expects_exit(service.exits, c.name, c.labels)
        return container_health(c, expected, c.id in stopped_on_purpose)

    return [c for c in service.containers if judged(c) == "down"]


def unwatched_view(
    down: list[ContainerSummary], seen: HostSightings, ended: Incident | None
) -> UnwatchedView:
    """Why the service opens nothing, the most telling reason first: a container paused, then
    one created and never started (what Docker shows now), then the incident it has stayed down
    through (`ended`), then a container already down when the watcher first listed the host;
    else unknown. `down` are its containers that show down."""
    first = down[0].name if down else None
    for state in ("paused", "created"):
        found = next((c for c in down if c.state == state), None)
        if found is not None:
            return UnwatchedView(reason=state, container=found.name)
    if ended is not None:
        return UnwatchedView(
            reason="after_incident",
            container=first,
            incident_id=ended.id,
            incident_short_name=ended.short_name,
        )
    before = next((c for c in down if c.id in seen.down_at_start), None)
    if before is not None:
        return UnwatchedView(reason="before_start", container=before.name)
    return UnwatchedView(reason="unknown", container=first)


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
        sightings: Sightings | None = None,
        health: HealthStore | None = None,
    ) -> None:
        """`watching` is the running watcher's `WatcherManager.watching`; without it no view
        says what the watcher waits on, nor why one shown down opens nothing (`unwatched`).
        `sightings` is what the watcher saw of the containers (shared with it), and `health` the
        health it stored; without them a container stopped on purpose that exited 137 shows
        down, and a reason needing them is not given."""
        self.registry = registry
        self.incidents = incidents
        self.quiet = quiet
        self.url_checks = url_checks
        self.clock = clock
        self._watching = watching
        self.sightings = sightings or Sightings()
        self._health = health

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
        checks: dict[str, Checks] = {}
        hosts = {h.config.id: h for h in self.registry.hosts()}
        views = []
        for service in self.registry.services(host_id):
            if service.host_id not in checks:
                checks[service.host_id] = await self.checks(service.host_id)
            host = hosts[service.host_id]
            views.append(await self.service(service, host, active, watched, checks[host.config.id]))
        return views

    async def service(
        self,
        service: Service,
        host: HostState,
        active: list[QuietState] | None = None,
        watched: Watched | None = None,
        checks: Checks | None = None,
    ) -> ServiceView:
        """One service as the overview shows it."""
        active = await self.quiet.active() if active is None else active
        watched = await self.watched() if watched is None else watched
        checks = await self.checks(service.host_id) if checks is None else checks
        subject = Subject(kind="service", host_id=service.host_id, service_id=service.id)
        seen = self.sightings.of(service.host_id)
        view = service_view(
            service,
            await self.incidents.open_incident_id(subject),
            checks,
            self.clock(),
            quiet_of(service, active),
            is_asleep(host),
            watched.get(subject.key),
            seen.stopped_on_purpose,
        )
        # Without the running watcher's `watching`, a service it is about to open an incident for
        # would look the same as one it never will.
        if self._watching is None or not is_unwatched(view, host):
            return view
        down = down_containers(service, seen.stopped_on_purpose)
        reason = unwatched_view(down, seen, await self._ended_while_down(service, seen))
        return view.model_copy(update={"unwatched": reason})

    async def _ended_while_down(self, service: Service, seen: HostSightings) -> Incident | None:
        """The service's latest incident, when it ended while this watcher was running and the
        service has been down since before it did (as the watcher stored its health), so it
        stayed down through it. A stored health is kept across a restart of Bleep, and says
        nothing about what happened while Bleep was not looking."""
        if self._health is None or seen.first_listing is None:
            return None
        latest = await self.incidents.list(service_id=service.id, limit=1)
        if not latest or latest[0].status not in TERMINAL_STATUSES:
            return None
        ended = latest[0].closed_at
        if ended is None or ended < seen.first_listing:
            return None
        stored = await self._health.current(service.id)
        if stored is None or stored[0] != "down" or stored[1] > ended:
            return None
        return latest[0]

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
