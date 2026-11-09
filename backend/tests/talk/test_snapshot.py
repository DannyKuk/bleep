"""The snapshot a chat answer comes from: Bleep's own hosts, services and incidents, built by
code from the stores the dashboard reads, with times in the configured zone."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from bleep.incidents.approvals import ApprovalStore
from bleep.incidents.bus import EventBus
from bleep.incidents.repository import IncidentRepo
from bleep.schemas.api import ContainerView, HostView, QuietView, ServiceView
from bleep.schemas.graph import Decision, PlanStep, Proposal
from bleep.schemas.incidents import Signal, Subject
from bleep.store.db import Database
from bleep.talk.state import BleepState

NOW = datetime(2026, 11, 9, 13, 12, tzinfo=UTC)  # 14:12 in Vienna
VIENNA = ZoneInfo("Europe/Vienna")
SHOP = Subject(kind="service", host_id="laptop", service_id="laptop/compose:shop")


class FakeHealth:
    def __init__(self, hosts: list[HostView], services: list[ServiceView]) -> None:
        self._hosts, self._services = hosts, services

    async def hosts(self) -> list[HostView]:
        return self._hosts

    async def services(self) -> list[ServiceView]:
        return self._services


def host(host_id: str, **fields) -> HostView:
    values = {
        "id": host_id,
        "name": host_id,
        "kind": "ssh",
        "reachable": True,
        "last_seen": NOW - timedelta(seconds=10),
        "error": None,
        "basics": None,
        "service_count": 1,
        "health": "ok",
    }
    return HostView(**(values | fields))


def service(name: str, health: str = "ok", **fields) -> ServiceView:
    values = {
        "id": f"laptop/compose:{name}",
        "host_id": "laptop",
        "name": name,
        "source": "compose",
        "health": health,
        "containers": [
            ContainerView(
                id="c1",
                name=f"{name}-web-1",
                image="img",
                state="exited",
                health="none",
                restart_count=2,
                exit_code=137,
                protected=False,
            )
        ],
        "urls": [],
        "open_incident_id": None,
    }
    return ServiceView(**(values | fields))


class World:
    db: Database
    repo: IncidentRepo
    approvals: ApprovalStore
    bus: EventBus
    clock_at: datetime

    def state(self, health: FakeHealth, zone=VIENNA) -> BleepState:
        return BleepState(
            health, self.repo, self.approvals, self.bus, zone=lambda: zone, clock=lambda: NOW
        )

    async def incident(self, subject: Subject, at: datetime, name: str = "shop"):
        self.clock_at = at
        incident, _ = await self.repo.open_or_attach(
            subject,
            [Signal(subject=subject, kind="container_died", at=at, container="shop-web-1")],
            name=name,
        )
        return incident


@pytest.fixture
async def world(tmp_path) -> World:
    w = World()
    w.db = Database(tmp_path / "bleep.sqlite")
    await w.db.migrate()
    w.clock_at = NOW
    w.repo = IncidentRepo(w.db, clock=lambda: w.clock_at)
    w.approvals = ApprovalStore(w.db, "s")
    w.bus = EventBus(w.db, clock=lambda: w.clock_at)
    return w


PLAN = Proposal(
    kind="act",
    steps=[PlanStep(action="docker.restart", host_id="laptop", target="shop-web-1")],
    reason="it exited (code 137).",
)


async def test_the_snapshot_lists_hosts_services_and_incidents_in_the_local_zone(world):
    quiet = QuietView(scope="host", mode="snooze", until=NOW + timedelta(hours=2), since=NOW)
    old = await world.incident(SHOP, NOW - timedelta(hours=40))
    await world.bus.publish(old.id, "report", {"outcome": "Fixed", "cause": "an old cause"})
    await world.repo.set_status(old.id, "resolved")
    world.clock_at = NOW - timedelta(hours=12)
    last_night = await world.incident(SHOP, NOW - timedelta(hours=12, minutes=7))
    await world.bus.publish(
        last_night.id,
        "report",
        {"outcome": "Fixed and verified", "cause": "shop-db was\nstopped.", "summary": "s"},
    )
    await world.repo.set_status(last_night.id, "resolved")
    now_open = await world.incident(SHOP, NOW - timedelta(minutes=10))
    await world.approvals.request(now_open.id, PLAN, [])
    health = FakeHealth(
        [host("laptop", health="down"), host("m1", reachable=False, quiet=quiet)],
        [service("shop", "down", open_incident_id=now_open.id), service("grafana")],
    )

    snapshot = await world.state(health).snapshot()

    assert (snapshot.now, snapshot.zone) == ("2026-11-09 14:12", "Europe/Vienna")
    laptop, m1 = snapshot.hosts
    assert (laptop.health, laptop.last_seen) == ("down", "2026-11-09 14:11")
    assert m1.quiet is not None and m1.quiet.until == "2026-11-09 16:12"
    assert not m1.reachable
    shop, grafana = snapshot.services
    assert (shop.health, shop.open_incident) == ("down", "shop #3")
    assert shop.containers[0].exit_code == 137 and shop.containers[0].restarts == 2
    assert grafana.open_incident is None
    [open_line] = snapshot.open_incidents
    assert (open_line.name, open_line.status, open_line.plan_waiting) == ("shop #3", "open", True)
    assert open_line.opened == "2026-11-09 14:02"
    [ended] = snapshot.ended_last_24h  # the one 40 h ago is left out
    assert (ended.name, ended.outcome) == ("shop #2", "Fixed and verified")
    assert ended.cause == "shop-db was stopped."  # one line
    assert ended.ended == "2026-11-09 02:05"


async def test_without_a_zone_the_times_are_utc(world):
    snapshot = await world.state(FakeHealth([host("laptop")], []), zone=None).snapshot()
    assert (snapshot.now, snapshot.zone) == ("2026-11-09 13:12", "UTC")


async def test_an_incident_that_ended_without_a_report_takes_its_last_note_as_outcome(world):
    incident = await world.incident(SHOP, NOW - timedelta(hours=1))
    await world.bus.publish(incident.id, "notice", {"text": "Closed: the service was made quiet."})
    await world.repo.close_unfinished(incident.id)

    [ended] = (await world.state(FakeHealth([], [])).snapshot()).ended_last_24h

    assert ended.outcome == "Closed: the service was made quiet."
    assert ended.cause is None


async def test_the_incident_a_message_is_about_carries_its_signals_plan_and_notes(world):
    incident = await world.incident(SHOP, NOW - timedelta(minutes=10))
    await world.bus.publish(incident.id, "notice", {"text": "Looking at the logs."})
    secret_plan = Proposal(
        kind="act",
        steps=[
            PlanStep(
                action="coolify.set_env",
                host_id="laptop",
                target="shop",
                params={"key": "DATABASE_URL", "value": "postgres://shop:hunter2@db/shop"},
            )
        ],
        reason="r",
    )
    await world.approvals.request(incident.id, secret_plan, [])

    facts = await world.state(FakeHealth([], [])).incident(incident.id)

    assert facts is not None
    assert facts.plan_waiting and facts.plan[0].action == "coolify.set_env"
    assert "hunter2" not in facts.model_dump_json()  # the plan as a chat shows it: redacted
    assert [(s.kind, s.container, s.at) for s in facts.signals] == [
        ("container_died", "shop-web-1", "2026-11-09 14:02")
    ]
    assert facts.notes == ["Looking at the logs."]


async def test_a_decided_plan_is_no_longer_waiting_and_is_not_shown(world):
    incident = await world.incident(SHOP, NOW - timedelta(minutes=10))
    request = await world.approvals.request(incident.id, PLAN, [])
    await world.approvals.resolve(
        incident.id, Decision(kind="reject", via="dashboard", at=NOW), request.id
    )

    facts = await world.state(FakeHealth([], [])).incident(incident.id)

    assert facts is not None and not facts.plan_waiting and facts.plan == []


async def test_an_unknown_incident_gives_none(world):
    assert await world.state(FakeHealth([], [])).incident("inc_nope") is None
