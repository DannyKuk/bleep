import asyncio
from datetime import UTC, datetime, timedelta

from bleep.hosts.config import ServiceOverrides
from bleep.hosts.engine import EngineUnavailable
from bleep.hosts.registry import Registry
from bleep.schemas.docker import ContainerSummary, DockerEvent
from bleep.schemas.incidents import Signal
from bleep.schemas.settings import Thresholds
from bleep.watcher import poller
from bleep.watcher.detector import Detector, OpenIncident, Outcome
from bleep.watcher.poller import HostWatcher
from fakes import FakeEngine, socket_host

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
PROJECT = "com.docker.compose.project"


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float = 15) -> None:
        self.now += timedelta(seconds=seconds)


def box(name, state="running", **fields) -> ContainerSummary:
    fields.setdefault("labels", {PROJECT: "shop"})
    return ContainerSummary(id=f"id-{name}", name=name, image="img", state=state, **fields)


def ev(action, name, at, **attributes) -> DockerEvent:
    return DockerEvent(
        time=at,
        type="container",
        action=action,
        container_id=f"id-{name}",
        container_name=name,
        attributes={"name": name, **attributes},
    )


class Feed:
    """Feeds the watcher's signals to a detector and ticks it with each snapshot, as the manager."""

    def __init__(self, detector: Detector, clock) -> None:
        self.detector = detector
        self._clock = clock

    async def sink(self, signal: Signal) -> None:
        self._record(self.detector.feed(signal))

    async def snapshot(self, host_id, states) -> None:
        self._record(self.detector.tick(self._clock(), states, host_id))

    def _record(self, outcomes: list[Outcome]) -> None:
        pass


class Rig:
    def __init__(self, containers) -> None:
        self.clock = Clock()
        self.engine = FakeEngine("h", containers, clock=self.clock)
        self.signals: list[Signal] = []
        self.snapshots: list[dict] = []

        async def connector(host):
            return None, self.engine

        self.registry = Registry([socket_host(id="h")], connector, clock=self.clock)
        self.watcher = HostWatcher(
            "h", self.registry, self.sink, self.clock, on_snapshot=self.snapshot
        )

    async def sink(self, signal: Signal) -> None:
        self.signals.append(signal)

    async def snapshot(self, host_id, states) -> None:
        self.snapshots.append(states)

    @property
    def deaths(self):
        return [s for s in self.signals if s.kind != "host_unreachable"]

    @property
    def probes(self):
        return [s.detail["ok"] for s in self.signals if s.kind == "host_unreachable"]

    async def tick(self, seconds: float = 15) -> bool:
        self.clock.advance(seconds)
        return await self.watcher.tick()


async def rig(*containers) -> Rig:
    r = Rig(list(containers))
    await r.registry.start()
    return r


async def test_a_tick_reports_events_in_the_window_once_with_the_service_subject():
    r = await rig(box("web-1"), box("db-1"))
    await r.tick()
    r.engine.history.append(ev("die", "web-1", T0 + timedelta(seconds=20), exitCode="1"))
    r.engine.container_list[0] = box("web-1", "exited", exit_code=1)
    assert await r.tick()
    assert await r.tick()  # the window moved on; the event is not seen again
    [signal] = r.deaths
    assert signal.kind == "container_died"
    assert signal.subject.service_id == "h/compose:shop"
    assert signal.detail["exit_code"] == 1
    assert r.probes == [True, True, True]


async def test_an_overlapping_window_does_not_repeat_an_event():
    r = await rig(box("web-1"))
    await r.tick()
    boundary = T0 + timedelta(seconds=15)  # exactly the end of tick 1 and the start of tick 2
    r.engine.history.append(ev("oom", "web-1", boundary))
    await r.tick()
    r.watcher._cursor = T0  # a retried tick replays from an older cursor
    await r.tick()
    assert [s.kind for s in r.deaths] == ["oom"]


async def test_a_host_unreachable_for_three_ticks_replays_the_gap_once_without_duplicates():
    r = await rig(box("web-1"))
    await r.tick()
    r.engine.drop()
    r.engine.history.append(ev("die", "web-1", T0 + timedelta(seconds=20), exitCode="1"))
    r.engine.history.append(ev("oom", "web-1", T0 + timedelta(seconds=50)))
    results = [await r.tick(), await r.tick(), await r.tick()]
    assert results == [False, False, False]
    assert r.deaths == []  # a failed tick marks nothing as dead
    assert r.probes == [True, False, False, False]
    r.engine.restore()
    assert await r.tick()
    assert await r.tick()
    assert [s.kind for s in r.deaths] == ["container_died", "oom"]
    assert r.probes[-2:] == [True, True]


async def test_the_cursor_stays_put_when_a_tick_fails():
    r = await rig(box("web-1"))
    start = r.watcher._cursor
    r.engine.drop()
    await r.tick()
    assert r.watcher._cursor == start
    r.engine.restore()
    await r.tick()
    assert r.watcher._cursor > start


async def test_bleep_self_containers_emit_nothing():
    own = box("bleep", labels={"bleep.self": "true"})
    r = await rig(box("web-1"), own)
    await r.tick()
    r.engine.history.append(ev("die", "bleep", T0 + timedelta(seconds=20), exitCode="1"))
    r.engine.container_list[1] = own.model_copy(update={"state": "exited", "exit_code": 1})
    del r.engine.container_list[0]  # and a gone self container in the diff
    r.engine.container_list.append(box("gone-self", labels={"bleep.self": "true"}))
    await r.tick()
    assert [s.container for s in r.deaths] == ["web-1"]  # only the real container that vanished


async def test_a_container_gone_between_ticks_emits_one_death_even_without_events():
    r = await rig(box("web-1", restart_policy="always"), box("db-1"))
    await r.tick()
    del r.engine.container_list[0]
    await r.tick()
    await r.tick()
    [signal] = r.deaths
    assert signal.container == "web-1"
    assert signal.kind == "container_died"
    assert signal.detail == {"exit_code": None, "restart_policy": "always"}


async def test_a_late_event_for_a_death_the_diff_found_is_not_a_second_signal():
    r = await rig(box("web-1"))
    await r.tick()
    r.engine.container_list[0] = box("web-1", "exited", exit_code=1)
    await r.tick()  # the diff reports it; the event is not visible yet
    r.engine.history.append(ev("die", "web-1", r.clock.now - timedelta(seconds=1), exitCode="1"))
    await r.tick()
    assert len(r.deaths) == 1


async def test_a_stopped_container_makes_no_signal_by_events_or_diff():
    r = await rig(box("web-1"), box("db-1"))
    await r.tick()
    at = T0 + timedelta(seconds=20)
    r.engine.history += [ev("die", "web-1", at, exitCode="0"), ev("stop", "web-1", at)]
    r.engine.container_list[0] = box("web-1", "exited", exit_code=0)
    r.engine.container_list[1] = box("db-1", "exited", exit_code=0)  # events missed
    await r.tick()
    assert r.deaths == []


async def test_the_snapshot_hook_gets_containers_by_service_id_on_every_good_tick():
    r = await rig(box("web-1"), box("solo", labels={}))
    await r.tick()
    r.engine.drop()
    await r.tick()
    assert len(r.snapshots) == 1
    assert set(r.snapshots[0]) == {"h/compose:shop", "h/container:solo"}


async def test_a_failing_sink_does_not_stop_the_tick():
    r = await rig(box("web-1"))
    calls = []

    async def broken(signal):
        calls.append(signal)
        raise RuntimeError("boom")

    r.watcher._sink = broken
    await r.tick()
    r.engine.history.append(ev("oom", "web-1", T0 + timedelta(seconds=20)))
    assert await r.tick()
    assert len(calls) == 3  # probe, probe, oom: all tried


async def test_a_hung_connect_fails_the_tick_fast(monkeypatch):
    monkeypatch.setattr(poller, "TICK_TIMEOUT_S", 0.05)
    clock = Clock()

    async def hanging(host):
        await asyncio.Event().wait()

    registry = Registry([socket_host(id="h")], hanging, clock=clock)
    signals: list[Signal] = []

    async def sink(signal):
        signals.append(signal)

    watcher = HostWatcher("h", registry, sink, clock)
    assert await asyncio.wait_for(watcher.tick(), 2) is False
    assert signals[0].detail == {"ok": False}


async def test_a_reconnect_that_fails_is_a_failed_tick_not_an_error():
    clock = Clock()

    async def refusing(host):
        raise EngineUnavailable(host.id, "connection refused")

    registry = Registry([socket_host(id="h")], refusing, clock=clock)
    watcher = HostWatcher("h", registry, lambda s: asyncio.sleep(0), clock)
    assert await watcher.tick() is False


async def test_the_feed_judges_recovery_on_the_snapshot_of_the_same_tick():
    r = await rig(box("web-1", restart_policy="always"), box("db-1"))
    feed = Feed(Detector(Thresholds(), r.clock), r.clock)
    r.watcher._sink, r.watcher._on_snapshot = feed.sink, feed.snapshot
    await r.tick()
    died_at = T0 + timedelta(seconds=20)
    r.engine.history.append(ev("oom", "web-1", died_at))
    r.engine.container_list[0] = box(
        "web-1", restart_policy="always", started_at=died_at + timedelta(seconds=2)
    )
    await r.tick()  # signal and a running, freshly started container arrive together
    assert feed.detector._pending == {}


async def test_a_container_that_stays_dead_opens_after_grace_through_the_feed():
    r = await rig(box("web-1", restart_policy="always"))
    outcomes = []
    feed = Feed(Detector(Thresholds(), r.clock), r.clock)
    real_record = feed._record
    feed._record = lambda o: (outcomes.extend(o), real_record(o))[1]
    r.watcher._sink, r.watcher._on_snapshot = feed.sink, feed.snapshot
    await r.tick()
    r.engine.history.append(ev("die", "web-1", T0 + timedelta(seconds=20), exitCode="1"))
    r.engine.container_list[0] = box("web-1", "exited", exit_code=1, restart_policy="always")
    await r.tick()
    assert outcomes == []
    await r.tick(60)
    [opened] = [o for o in outcomes if isinstance(o, OpenIncident)]
    assert opened.subject.service_id == "h/compose:shop"


async def test_a_die_event_stamped_between_the_window_and_the_listing_is_not_a_second_death():
    r = await rig(box("web-1"))
    await r.tick()
    r.engine.container_list[0] = box("web-1", "exited", exit_code=1)
    real_refresh = r.registry.refresh

    async def slow_refresh(host_id):
        r.clock.advance(1)  # the listing is taken after the event window closed
        await real_refresh(host_id)

    r.registry.refresh = slow_refresh
    await r.tick()  # the diff reports the death
    until = r.clock.now - timedelta(seconds=1)
    r.engine.history.append(ev("die", "web-1", until + timedelta(milliseconds=200), exitCode="1"))
    r.registry.refresh = real_refresh
    await r.tick()
    assert len(r.deaths) == 1


async def test_a_restart_the_diff_caught_alone_is_a_recovery_not_an_incident():
    r = await rig(box("web-1", restart_policy="always"))
    outcomes = []
    feed = Feed(Detector(Thresholds(), r.clock), r.clock)
    real_record = feed._record
    feed._record = lambda o: (outcomes.extend(o), real_record(o))[1]
    r.watcher._sink, r.watcher._on_snapshot = feed.sink, feed.snapshot
    await r.tick()
    r.engine.container_list[0] = box(
        "web-1",
        restart_policy="always",
        restart_count=1,
        started_at=T0 + timedelta(seconds=22),
    )
    await r.tick()  # events missed it; only the restart count shows
    await r.tick(600)  # well past any grace
    assert [o for o in outcomes if isinstance(o, OpenIncident)] == []


async def test_a_job_named_in_exits_is_marked_and_a_run_rm_container_joins_its_service():
    job = {PROJECT: "shop", "com.docker.compose.service": "fetch"}
    r = await rig(box("shop-web-1"), box("shop-fetch-1", "exited", exit_code=0, labels=job))
    r.registry.override("h", [], {"compose:shop": ServiceOverrides(exits=["fetch"])})
    await r.tick()
    run = "shop-fetch-run-ab12"  # never listed: it lived between two listings
    at = T0 + timedelta(seconds=20)
    r.engine.history += [
        ev("die", run, at, exitCode="0", **job),
        ev("destroy", run, at + timedelta(seconds=1), **job),
    ]
    await r.tick()
    [signal] = r.deaths
    assert signal.subject.service_id == "h/compose:shop"  # by its labels, not container:<name>
    assert signal.detail["expected_exit"] is True
    assert signal.detail["compose_service"] == "fetch"
