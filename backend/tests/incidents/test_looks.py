"""A look on request opens exactly one incident through the watcher's path, and opens none for a
service with an open incident, a quiet one, an unknown one, Bleep's own, one whose host does not
answer, or once the day's cost cap is spent (#403)."""

import asyncio
from datetime import timedelta
from typing import Literal

from bleep.incidents.looks import Looks
from bleep.incidents.quiet import QuietStore
from bleep.incidents.repository import Repeats
from bleep.llm.usage import CostLedger
from bleep.schemas.incidents import Incident, Signal, Subject
from bleep.schemas.services import Service
from bleep.schemas.settings import Budgets
from fakes import FakeEngine, compose_labels
from graph_env import T0, box, graph_env

VIA: tuple[Literal["telegram", "dashboard"], ...] = ("telegram", "dashboard")


class Runs:
    def __init__(self) -> None:
        self.started: list[str] = []

    async def start(self, incident: Incident) -> None:
        self.started.append(incident.id)


async def rig(tmp_path, *, budgets: Budgets | None = None, engine: FakeEngine | None = None):
    env = await graph_env(tmp_path, engine=engine)
    quiet, runs = QuietStore(env.db, clock=lambda: T0), Runs()
    looks = Looks(
        env.repo,
        runs,
        quiet,
        env.registry,
        ledger=CostLedger(env.db),
        budgets=lambda: budgets or Budgets(),
        clock=lambda: T0,
    )
    return env, looks, quiet, runs


def shop(env) -> Subject:
    return Subject(kind="service", host_id="a", service_id=env.service_id("shop"))


async def test_a_look_opens_one_incident_with_a_look_signal_and_starts_its_run(tmp_path):
    env, looks, _, runs = await rig(tmp_path)

    look = await looks.look(env.service_id("shop"), "telegram")

    assert look.outcome == "opened" and look.incident is not None
    assert (look.name, look.host_id) == ("shop", "a")
    assert look.incident.short_name == "shop #1"
    assert look.incident.title == "look requested on shop"  # the name, not the id (#421)
    [signal] = await env.repo.signals(look.incident.id)
    assert (signal.kind, signal.detail, signal.container) == (
        "look_requested",
        {"via": "telegram"},
        None,
    )
    assert runs.started == [look.incident.id]
    assert len(await env.repo.list()) == 1


async def test_a_look_at_a_coolify_app_is_titled_by_its_name_not_its_uuid(tmp_path):
    uuid = "vclhwoxxy60z4vk70g0vpesq"
    labels = {
        "coolify.type": "application",
        "coolify.applicationUuid": uuid,
        "coolify.resourceName": "dkwebsite",
    }
    engine = FakeEngine("a", [box(f"{uuid}-120000000000", labels=labels)])
    env, looks, _, _ = await rig(tmp_path, engine=engine)
    [service] = env.registry.services("a")
    assert service.id == f"a/{uuid}"

    look = await looks.look(service.id, "telegram")

    assert look.incident is not None
    assert look.incident.title == "look requested on dkwebsite"
    assert look.incident.short_name == "dkwebsite #1"


async def test_a_watcher_incident_keeps_its_title_by_service_id(tmp_path):
    env, _, _, _ = await rig(tmp_path)
    subject = shop(env)
    died = Signal(subject=subject, kind="container_died", at=T0, container="shop-web-1")

    incident, _ = await env.repo.open_or_attach(subject, [died], name="shop")

    assert incident.title == f"container died on {env.service_id('shop')}"


async def test_a_second_look_opens_nothing_and_names_the_open_one(tmp_path):
    env, looks, _, runs = await rig(tmp_path)
    first = await looks.look(env.service_id("shop"), "telegram")

    again = await looks.look(env.service_id("shop"), "dashboard")

    assert again.outcome == "open" and again.incident is not None
    assert again.incident.id == first.incident.id
    assert len(await env.repo.signals(first.incident.id)) == 1  # nothing attached to it
    assert len(runs.started) == 1 and len(await env.repo.list()) == 1


async def test_two_looks_at_once_open_one_incident(tmp_path):
    env, looks, _, runs = await rig(tmp_path)

    both = await asyncio.gather(*(looks.look(env.service_id("shop"), v) for v in VIA))

    assert sorted(look.outcome for look in both) == ["open", "opened"]
    assert len({look.incident.id for look in both}) == 1
    assert len(runs.started) == 1 and len(await env.repo.list()) == 1


async def test_an_incident_opened_after_the_check_is_named_and_starts_nothing(tmp_path):
    """The write lock decides: an incident that opened between the look's check and its write
    takes no look signal, and the look starts no run."""
    env, looks, _, runs = await rig(tmp_path)
    died = await env.open(shop(env), container="shop-web-1")

    async def nothing_open(_subject: Subject) -> None:
        return None  # what the check saw just before the watcher's write

    env.repo.open_target = nothing_open  # type: ignore[method-assign]
    look = await looks.look(env.service_id("shop"), "telegram")

    assert (look.outcome, look.incident.id) == ("open", died.id)
    assert [s.kind for s in await env.repo.signals(died.id)] == ["container_died"]
    assert runs.started == []


async def test_an_incident_the_watcher_opened_is_named_and_takes_no_look_signal(tmp_path):
    env, looks, _, runs = await rig(tmp_path)
    died = await env.open(shop(env), container="shop-web-1")

    look = await looks.look(env.service_id("shop"), "telegram")

    assert (look.outcome, look.incident.id) == ("open", died.id)
    assert [s.kind for s in await env.repo.signals(died.id)] == ["container_died"]
    assert runs.started == []


async def test_a_waiting_host_incident_covers_the_service(tmp_path):
    env, looks, _, runs = await rig(tmp_path)
    host = await env.open(Subject(kind="host", host_id="a"), "host_unreachable")

    look = await looks.look(env.service_id("shop"), "telegram")

    assert (look.outcome, look.incident.id) == ("open", host.id)
    assert runs.started == [] and len(await env.repo.list()) == 1


async def test_a_quiet_service_or_host_opens_nothing(tmp_path):
    env, looks, quiet, runs = await rig(tmp_path)
    await quiet.set("service", env.service_id("shop"), "snooze", T0 + timedelta(hours=2))

    snoozed = await looks.look(env.service_id("shop"), "telegram")
    await quiet.clear("service", env.service_id("shop"))
    await quiet.set("host", "a", "leave_alone", None)
    left = await looks.look(env.service_id("shop"), "telegram")

    assert snoozed.outcome == "quiet" and snoozed.quiet.until == T0 + timedelta(hours=2)
    assert left.outcome == "quiet" and left.quiet.scope == "host"
    assert runs.started == [] and await env.repo.list() == []


async def test_an_unknown_service_opens_nothing(tmp_path):
    env, looks, _, runs = await rig(tmp_path)

    look = await looks.look("a/compose:shoop", "telegram")

    assert (look.outcome, look.incident) == ("unknown", None)
    assert runs.started == [] and await env.repo.list() == []


async def test_bleeps_own_containers_are_never_looked_at(tmp_path):
    env, _, quiet, runs = await rig(tmp_path)
    own = box("bleep-backend-1", "bleep", labels={**compose_labels("bleep"), "bleep.self": "true"})
    service = Service(
        id="a/compose:bleep", host_id="a", name="bleep", source="compose", containers=[own]
    )

    class Registry:
        def service(self, service_id: str) -> Service | None:
            return service if service_id == service.id else None

    looks = Looks(env.repo, runs, quiet, Registry())  # type: ignore[arg-type]

    look = await looks.look("a/compose:bleep", "telegram")

    assert look.outcome == "unknown"
    assert runs.started == [] and await env.repo.list() == []


async def test_a_host_that_does_not_answer_opens_nothing(tmp_path):
    env, looks, _, runs = await rig(tmp_path)
    env.engine.drop()
    await env.registry.refresh("a")

    look = await looks.look(env.service_id("shop"), "telegram")

    assert look.outcome == "unreachable"
    assert runs.started == [] and await env.repo.list() == []


async def test_a_spent_daily_cap_opens_nothing(tmp_path):
    env, looks, _, runs = await rig(tmp_path, budgets=Budgets(daily_cost_usd=0.5))
    await CostLedger(env.db).add("inc_other", "m", 1000, 1000, 0.6)

    look = await looks.look(env.service_id("shop"), "telegram")

    assert look.outcome == "spent"
    assert runs.started == [] and await env.repo.list() == []


async def test_looks_never_fold_so_each_one_is_announced(tmp_path):
    env, looks, _, _ = await rig(tmp_path)
    first = await looks.look(env.service_id("shop"), "telegram")
    await env.repo.set_status(first.incident.id, "closed")

    second = await looks.look(env.service_id("shop"), "telegram")

    assert second.outcome == "opened"
    assert second.incident.fold_of is None and second.incident.short_name == "shop #2"
    assert "seen" not in second.incident.title


async def test_a_look_holds_no_failure_back(tmp_path):
    """The watcher's repeat holds count the incidents a failure outlived; a look is not one, so a
    look neither starts nor lengthens a hold (#356, #366)."""
    env, looks, _, _ = await rig(tmp_path)
    look = await looks.look(env.service_id("shop"), "telegram")
    await env.repo.set_status(look.incident.id, "closed")

    assert await env.repo.finished_since(shop(env), T0 - timedelta(hours=1)) is None

    failed = Signal(subject=shop(env), kind="url_failed", at=T0, detail={"url": "http://x"})
    url, _ = await env.repo.open_or_attach(shop(env), [failed])
    await env.repo.set_status(url.id, "closed")
    held = await env.repo.finished_since(shop(env), T0 - timedelta(hours=1))
    assert held is not None and (held.count, held.last_id) == (1, url.id)
    assert isinstance(held, Repeats)
