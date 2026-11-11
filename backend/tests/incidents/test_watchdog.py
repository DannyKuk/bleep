from datetime import UTC, datetime, timedelta

import pytest

from bleep.graph.deps import StaticSettings
from bleep.graph.shown import english
from bleep.incidents.bus import EventBus
from bleep.incidents.repository import IncidentRepo
from bleep.incidents.watchdog import Watchdog
from bleep.schemas.incidents import Signal, Subject
from bleep.schemas.settings import Budgets
from bleep.schemas.shown import Shown
from bleep.store.db import Database

T0 = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def after(self, minutes: int) -> datetime:
        self.now = T0 + timedelta(minutes=minutes)
        return self.now


class Channel:
    def __init__(self) -> None:
        self.notices: list[tuple[str, str]] = []

    async def notice(self, incident_id: str, text: Shown) -> None:
        self.notices.append((incident_id, english(text)))


@pytest.fixture
async def world(tmp_path):
    db = Database(tmp_path / "bleep.sqlite")
    await db.migrate()
    clock = Clock()
    repo, bus, channel = IncidentRepo(db, clock), EventBus(db, clock), Channel()
    subject = Subject(kind="service", host_id="a", service_id="a/shop")
    incident, _ = await repo.open_or_attach(
        subject, [Signal(subject=subject, kind="container_died", at=T0)]
    )
    settings = StaticSettings(budget_limits=Budgets(stuck_after_min=30))
    dog = Watchdog(repo, bus, channel, settings, clock)
    return incident, repo, bus, channel, clock, dog


async def dashboard_notices(bus: EventBus, incident_id: str) -> list[str]:
    return [e.payload["text"] for e in await bus.history(incident_id) if e.kind == "notice"]


async def test_an_incident_that_has_not_moved_gets_exactly_one_notice_per_stretch(world):
    incident, repo, bus, channel, clock, dog = world
    await bus.publish(incident.id, "node_started", {"node": "investigate"})
    await repo.set_status(incident.id, "investigating")

    assert await dog.tick(clock.after(29)) == []
    assert await dog.tick(clock.after(31)) == [incident.id]
    assert await dog.tick(clock.after(45)) == []
    assert await dog.tick(clock.after(120)) == []

    [text] = await dashboard_notices(bus, incident.id)
    assert "31 minutes" in text and channel.notices == [(incident.id, text)]


async def test_a_second_stretch_after_the_incident_moves_gets_one_more_notice(world):
    incident, repo, bus, channel, clock, dog = world
    await repo.set_status(incident.id, "investigating")
    await dog.tick(clock.after(31))  # stuck since opening
    clock.after(40)
    await bus.publish(incident.id, "node_started", {"node": "diagnose"})  # it moves

    assert await dog.tick(clock.after(60)) == []  # 20 minutes since it moved
    assert await dog.tick(clock.after(71)) == [incident.id]
    assert await dog.tick(clock.after(100)) == []
    assert len(await dashboard_notices(bus, incident.id)) == 2 and len(channel.notices) == 2


async def test_its_own_notices_do_not_count_as_the_incident_moving(world):
    incident, repo, bus, channel, clock, dog = world
    await dog.tick(clock.after(31))
    await bus.publish(incident.id, "notice", {"text": "something else"})
    assert await dog.tick(clock.after(62)) == []


@pytest.mark.parametrize("status", ["awaiting_approval", "paused", "resolved", "closed", "failed"])
async def test_incidents_waiting_for_a_human_or_paused_or_finished_are_left_alone(world, status):
    incident, repo, bus, channel, clock, dog = world
    await repo.set_status(incident.id, status)
    assert await dog.tick(clock.after(500)) == []
    assert channel.notices == []


async def test_a_channel_outage_does_not_hide_the_notice_from_the_dashboard(world):
    incident, repo, bus, channel, clock, dog = world

    async def down(incident_id: str, text: str) -> None:
        raise RuntimeError("telegram is down")

    channel.notice = down  # type: ignore[method-assign]
    assert await dog.tick(clock.after(31)) == [incident.id]
    assert len(await dashboard_notices(bus, incident.id)) == 1


async def test_one_incident_failing_does_not_skip_the_others(world):
    incident, repo, bus, channel, clock, dog = world
    other = Subject(kind="service", host_id="a", service_id="a/db")
    second, _ = await repo.open_or_attach(
        other, [Signal(subject=other, kind="container_died", at=T0)]
    )
    publish = bus.publish

    async def flaky(incident_id, kind, payload=None):
        if incident_id == incident.id:
            raise RuntimeError("database is locked")
        return await publish(incident_id, kind, payload)

    bus.publish = flaky  # type: ignore[method-assign]

    assert await dog.tick(clock.after(31)) == [second.id]


async def test_an_incident_the_runner_is_retrying_is_left_alone(world):
    incident, repo, bus, channel, clock, dog = world
    await repo.set_status(incident.id, "investigating")
    dog.skip = lambda incident_id: incident_id == incident.id

    assert await dog.tick(clock.after(45)) == []
    assert channel.notices == []
    dog.skip = lambda incident_id: False
    assert await dog.tick(clock.after(46)) == [incident.id]
