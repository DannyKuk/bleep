"""A service that shows down but that the watcher will open no incident for says why (#478), and
a container stopped on purpose shows stopped, judged from what the running watcher saw."""

from datetime import UTC, datetime, timedelta

import httpx

from bleep.hosts.dashboard import HostChange
from bleep.hosts.registry import Registry
from bleep.incidents.bus import EventBus
from bleep.incidents.quiet import QuietStore
from bleep.incidents.repository import IncidentRepo
from bleep.schemas.docker import ContainerSummary, DockerEvent
from bleep.schemas.incidents import Incident, Signal, Subject
from bleep.schemas.settings import Thresholds
from bleep.store.db import Database
from bleep.watcher.detector import Detector
from bleep.watcher.health_store import HealthStore
from bleep.watcher.http_checks import UrlCheckStore
from bleep.watcher.manager import WatcherManager
from bleep.watcher.overview import Overview
from bleep.watcher.sightings import HostSightings
from fakes import FakeEngine, compose_labels, socket_host

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
SHOP = "h/compose:shop"
SHOP_SUBJECT = Subject(kind="service", host_id="h", service_id=SHOP)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


class Runner:
    def __init__(self) -> None:
        self.started: list[Incident] = []

    async def start(self, incident: Incident) -> None:
        self.started.append(incident)

    async def recover_on_boot(self) -> list[str]:
        return []

    async def host_back(self, host_id: str) -> None:
        return None


def box(name: str, **fields) -> ContainerSummary:
    project = name.split("-")[0]
    fields.setdefault("state", "running")
    return ContainerSummary(
        id=f"id-{name}", name=name, image="img", labels=compose_labels(project), **fields
    )


def event(action: str, name: str, at: datetime, **attributes: str) -> DockerEvent:
    return DockerEvent(
        time=at,
        type="container",
        action=action,
        container_id=f"id-{name}",
        container_name=name,
        attributes={"name": name, **attributes},
    )


class Rig:
    """One host with shop and blog, the real manager and pull watcher on a fake engine, and the
    overview the hosts API builds on them."""

    def __init__(self, tmp_path, shop: ContainerSummary | None = None) -> None:
        self.clock = Clock()
        self.engine = FakeEngine("h", [shop or box("shop-web-1"), box("blog-web-1")])
        self.db = Database(tmp_path / "bleep.sqlite")
        self.repo = IncidentRepo(self.db, self.clock)
        self.quiet = QuietStore(self.db, self.clock)
        self.health = HealthStore(self.db)
        self.checks = UrlCheckStore(self.db)
        self.runner = Runner()

        async def connect(_host):
            return None, self.engine

        self.registry = Registry([socket_host(id="h")], connect, clock=self.clock)
        self.manager = WatcherManager(
            self.registry,
            Detector(Thresholds(), self.clock),
            self.repo,
            self.runner,  # type: ignore[arg-type]
            EventBus(self.db, self.clock),
            clock=self.clock,
            http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
            store=self.checks,
            quiet=self.quiet,
            health=self.health,
        )
        self.overview = Overview(
            self.registry,
            self.repo,
            self.quiet,
            self.checks,
            clock=self.clock,
            watching=self.manager.watching,
            sightings=self.manager.sightings,
            health=self.health,
        )

    async def boot(self) -> Rig:
        await self.db.migrate()
        await self.registry.start()
        await self.pulse()
        return self

    async def pulse(self, times: int = 1) -> None:
        for _ in range(times):
            self.clock.now += timedelta(seconds=15)
            await self.manager.hosts["h"].watcher.tick()

    def shop_is(self, **fields) -> None:
        self.engine.container_list[0] = box("shop-web-1", **fields)

    async def shop(self):
        [view] = [v for v in await self.overview.services() if v.id == SHOP]
        return view

    async def crash_past_grace(self) -> None:
        self.engine.history.append(
            event("die", "shop-web-1", self.clock.now + timedelta(seconds=1), exitCode="1")
        )
        self.shop_is(state="exited", exit_code=1)
        await self.pulse(6)


async def test_a_container_stopped_on_purpose_that_ignored_sigterm_shows_stopped(tmp_path):
    r = await Rig(tmp_path).boot()
    at = r.clock.now + timedelta(seconds=1)
    r.engine.history += [
        event("kill", "shop-web-1", at, signal="15"),
        event("kill", "shop-web-1", at + timedelta(seconds=10), signal="9"),
        event("die", "shop-web-1", at + timedelta(seconds=10), exitCode="137"),
        event("stop", "shop-web-1", at + timedelta(seconds=10)),
    ]
    r.shop_is(state="exited", exit_code=137)
    await r.pulse(6)
    view = await r.shop()
    assert view.health == "stopped"
    assert view.unwatched is None and view.watching == []
    stored = await r.health.current(SHOP)
    assert stored is not None and stored[0] == "stopped"  # the service page's "since" agrees
    assert r.runner.started == []


async def test_an_exit_137_without_a_stop_event_is_still_a_crash(tmp_path):
    r = await Rig(tmp_path).boot()
    at = r.clock.now + timedelta(seconds=1)
    r.engine.history.append(event("die", "shop-web-1", at, exitCode="137"))
    r.shop_is(state="exited", exit_code=137)
    await r.pulse()
    view = await r.shop()
    assert view.health == "down"
    assert [w.kind for w in view.watching] == ["container"]
    assert view.unwatched is None


async def test_a_stop_is_forgotten_once_the_container_starts_again(tmp_path):
    r = await Rig(tmp_path).boot()
    at = r.clock.now + timedelta(seconds=1)
    r.engine.history.append(event("stop", "shop-web-1", at))
    r.shop_is(state="exited", exit_code=137)
    await r.pulse()
    assert (await r.shop()).health == "stopped"
    r.engine.history.append(event("start", "shop-web-1", r.clock.now + timedelta(seconds=1)))
    r.shop_is()
    await r.pulse()
    r.engine.history.append(
        event("die", "shop-web-1", r.clock.now + timedelta(seconds=1), exitCode="137")
    )
    r.shop_is(state="exited", exit_code=137)  # the OOM killer this time
    await r.pulse()
    view = await r.shop()
    assert view.health == "down"
    assert [w.kind for w in view.watching] == ["container"]


async def test_a_paused_container_says_it_is_paused(tmp_path):
    r = await Rig(tmp_path).boot()
    r.shop_is(state="paused")
    await r.pulse(6)
    view = await r.shop()
    assert view.health == "down" and view.watching == []
    assert view.unwatched is not None
    assert (view.unwatched.reason, view.unwatched.container) == ("paused", "shop-web-1")
    assert r.runner.started == []


async def test_a_container_created_but_never_started_says_so(tmp_path):
    r = await Rig(tmp_path, shop=box("shop-web-1", state="created")).boot()
    view = await r.shop()
    assert view.unwatched is not None
    assert (view.unwatched.reason, view.unwatched.container) == ("created", "shop-web-1")


async def test_a_container_down_before_the_watcher_started_says_so(tmp_path):
    r = await Rig(tmp_path, shop=box("shop-web-1", state="exited", exit_code=1)).boot()
    await r.pulse(6)
    view = await r.shop()
    assert view.health == "down" and view.watching == []
    assert view.unwatched is not None
    assert (view.unwatched.reason, view.unwatched.container) == ("before_start", "shop-web-1")
    assert r.runner.started == []


async def test_a_service_still_down_after_its_incident_ended_names_that_incident(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.crash_past_grace()
    [incident] = r.runner.started
    assert (await r.shop()).unwatched is None  # its incident is open
    await r.pulse()
    await r.repo.set_status(incident.id, "failed")
    await r.pulse(2)
    view = await r.shop()
    assert view.health == "down" and view.watching == []
    assert view.unwatched is not None
    assert view.unwatched.reason == "after_incident"
    assert view.unwatched.incident_id == incident.id
    assert view.unwatched.incident_short_name == "shop #1"
    assert view.unwatched.container == "shop-web-1"


async def test_an_ended_incident_is_named_only_while_the_service_stayed_down_through_it(
    tmp_path,
):
    r = await Rig(tmp_path, shop=box("shop-web-1", state="exited", exit_code=1)).boot()
    death = Signal(
        kind="container_died",
        subject=SHOP_SUBJECT,
        at=r.clock.now,
        container="shop-web-1",
        detail={"exit_code": 1},
    )
    looked, _ = await r.repo.open_or_attach(SHOP_SUBJECT, [death], name="shop")
    r.clock.now += timedelta(minutes=1)
    await r.repo.set_status(looked.id, "failed")
    view = await r.shop()
    assert view.unwatched is not None
    assert (view.unwatched.reason, view.unwatched.incident_id) == ("after_incident", looked.id)
    # It came back and went down again since: the incident says nothing about this time.
    await r.health.record("h", SHOP, "ok", r.clock.now + timedelta(seconds=1))
    await r.health.record("h", SHOP, "down", r.clock.now + timedelta(seconds=2))
    view = await r.shop()
    assert view.unwatched is not None
    assert (view.unwatched.reason, view.unwatched.incident_id) == ("before_start", None)


async def test_after_a_restart_a_stop_on_purpose_that_exited_137_was_down_before_start(
    tmp_path,
):
    # A fresh watcher never saw the stop event: the container is down in its first listing.
    r = await Rig(tmp_path, shop=box("shop-web-1", state="exited", exit_code=137)).boot()
    view = await r.shop()
    assert view.health == "down"
    assert view.unwatched is not None
    assert (view.unwatched.reason, view.unwatched.container) == ("before_start", "shop-web-1")


async def test_an_incident_that_ended_before_the_watcher_started_is_not_named(tmp_path):
    r = Rig(tmp_path, shop=box("shop-web-1", state="exited", exit_code=1))
    await r.db.migrate()
    death = Signal(
        kind="container_died",
        subject=SHOP_SUBJECT,
        at=r.clock.now,
        container="shop-web-1",
        detail={"exit_code": 1},
    )
    # Before a restart of Bleep: the incident ended while its stored health said down.
    await r.health.record("h", SHOP, "down", r.clock.now)
    old, _ = await r.repo.open_or_attach(SHOP_SUBJECT, [death], name="shop")
    r.clock.now += timedelta(minutes=1)
    await r.repo.set_status(old.id, "failed")
    r.clock.now += timedelta(hours=1)
    await r.registry.start()
    await r.pulse()
    stored = await r.health.current(SHOP)
    assert stored is not None and stored[0] == "down"  # the stale "since" is still there
    view = await r.shop()
    assert view.unwatched is not None
    assert (view.unwatched.reason, view.unwatched.incident_id) == ("before_start", None)


async def test_a_down_service_the_watcher_waits_on_or_that_is_quiet_or_open_has_no_reason(
    tmp_path,
):
    r = await Rig(tmp_path).boot()
    r.engine.history.append(
        event("die", "shop-web-1", r.clock.now + timedelta(seconds=1), exitCode="1")
    )
    r.shop_is(state="exited", exit_code=1)
    await r.pulse()
    assert (await r.shop()).unwatched is None  # inside its grace window: watched
    await r.quiet.set("service", SHOP, "leave_alone", None)
    await r.pulse(6)
    view = await r.shop()
    assert view.watching == [] and view.unwatched is None  # quiet: it asked not to be told
    assert r.runner.started == []


async def test_an_unreachable_host_gives_its_services_no_reason(tmp_path):
    r = await Rig(tmp_path, shop=box("shop-web-1", state="exited", exit_code=1)).boot()
    r.engine.drop()
    await r.registry.refresh("h")
    assert (await r.shop()).unwatched is None


async def test_without_what_the_watcher_waits_on_no_reason_is_given(tmp_path):
    r = await Rig(tmp_path).boot()
    r.engine.history.append(
        event("die", "shop-web-1", r.clock.now + timedelta(seconds=1), exitCode="1")
    )
    r.shop_is(state="exited", exit_code=1)
    await r.pulse()  # inside its grace window: an incident is about to open
    talk = Overview(
        r.registry, r.repo, r.quiet, r.checks, sightings=r.manager.sightings, health=r.health
    )
    [view] = [v for v in await talk.services() if v.id == SHOP]
    assert view.health == "down"
    assert view.unwatched is None


async def test_an_overview_sharing_the_sightings_judges_a_stop_as_the_page_does(tmp_path):
    r = await Rig(tmp_path).boot()
    r.engine.history.append(event("stop", "shop-web-1", r.clock.now + timedelta(seconds=1)))
    r.shop_is(state="exited", exit_code=137)
    await r.pulse()
    talk = Overview(r.registry, r.repo, r.quiet, r.checks, sightings=r.manager.sightings)
    bare = Overview(r.registry, r.repo, r.quiet, r.checks)
    assert [v.health for v in await talk.services() if v.id == SHOP] == ["stopped"]
    assert [v.health for v in await bare.services() if v.id == SHOP] == ["down"]


async def test_the_service_route_and_the_list_agree(tmp_path):
    r = await Rig(tmp_path, shop=box("shop-web-1", state="created")).boot()
    service = r.registry.service(SHOP)
    [host] = r.registry.hosts()
    assert service is not None
    assert await r.overview.service(service, host) == await r.shop()


async def test_a_removed_host_forgets_what_its_watcher_saw(tmp_path):
    r = await Rig(tmp_path, shop=box("shop-web-1", state="exited", exit_code=1)).boot()
    assert r.manager.sightings.of("h").down_at_start == {"id-shop-web-1"}
    await r.manager.host_changed(HostChange("h", "removed", None))
    assert r.manager.sightings.of("h") == HostSightings()
