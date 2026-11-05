""" "This is expected": what it records, when it is offered, and how it races a decision."""

from datetime import UTC, datetime

from pydantic import SecretStr

from bleep.graph.build import build_graph, open_checkpointer
from bleep.incidents.expectations import CLOSED_NOTICE, Answer, Expectations, expectation_for
from bleep.incidents.runner import IncidentRunner
from bleep.incidents.settings_store import SettingsStore
from bleep.plugins.registry import PluginRegistry
from bleep.schemas.graph import Decision, PlanStep, Proposal, VerificationPlan
from bleep.schemas.incidents import Signal, Subject
from bleep.schemas.settings import ModelSettings
from graph_env import T0, graph_env

PLAN = Proposal(
    kind="act",
    steps=[PlanStep(action="docker.restart", host_id="a", target="shop-web-1")],
    reason="it exited",
    verification=VerificationPlan(containers=["shop-web-1"]),
)


def shop(env) -> Subject:
    return Subject(kind="service", host_id="a", service_id=env.service_id("shop"))


def death(subject: Subject, exit_code: int | None, **detail) -> Signal:
    return Signal(
        subject=subject,
        kind="container_died",
        at=T0,
        container="shop-web-1",
        detail={"exit_code": exit_code, "restart_policy": "on-failure", **detail},
    )


async def opened(env, *signals: Signal):
    incident, _ = await env.repo.open_or_attach(signals[0].subject, list(signals))
    return incident


class Rig:
    def __init__(self, env, runner: IncidentRunner) -> None:
        plugins = PluginRegistry([], SecretStr("s"), env={})
        self.settings = SettingsStore(env.db, env.registry, plugins, models=ModelSettings())
        self.expectations = Expectations(
            env.repo, runner, env.deps.approvals, self.settings, env.registry, env.bus
        )


async def events(env, incident_id: str, kind: str) -> list[str]:
    return [e.payload["text"] for e in await env.bus.history(incident_id) if e.kind == kind]


async def test_a_jobs_clean_exit_is_recorded_in_exits_and_the_incident_closes(tmp_path):
    env = await graph_env(tmp_path)
    late = Signal(subject=shop(env), kind="down_past_grace", at=T0, container="shop-web-1")
    incident = await opened(env, death(shop(env), 0, compose_service="fetch"), late)
    async with open_checkpointer(tmp_path / "c.sqlite") as saver:
        rig = Rig(env, IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus))
        offered = await rig.expectations.offered(incident.id)
        assert offered is not None and offered.entries == ("fetch",)
        answer = await rig.expectations.expected(incident.id, "dashboard")

    assert answer.done and "fetch on shop is expected to exit" in answer.text
    assert env.registry.service(shop(env).service_id).exits == ["fetch"]
    assert (await env.repo.get(incident.id)).status == "closed"
    assert await events(env, incident.id, "expected") == [answer.text]
    assert CLOSED_NOTICE in await events(env, incident.id, "notice")
    assert await rig.expectations.offered(incident.id) is None  # closed: offered no more


async def test_a_crash_is_never_expected(tmp_path):
    env = await graph_env(tmp_path)
    for code in (1, None):
        incident = await opened(env, death(shop(env), code))
        assert expectation_for(incident, await env.repo.signals(incident.id), env.registry) is None
        await env.repo.close_unfinished(incident.id)
    unhealthy = Signal(subject=shop(env), kind="unhealthy", at=T0, container="shop-web-1")
    incident = await opened(env, death(shop(env), 0), unhealthy)
    assert expectation_for(incident, await env.repo.signals(incident.id), env.registry) is None


async def test_an_unreachable_host_is_expected_to_sleep(tmp_path):
    env = await graph_env(tmp_path)
    host = Subject(kind="host", host_id="a")
    incident = await opened(
        env, Signal(subject=host, kind="host_unreachable", at=T0, detail={"ok": False})
    )
    async with open_checkpointer(tmp_path / "c.sqlite") as saver:
        rig = Rig(env, IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus))
        answer = await rig.expectations.expected(incident.id, "telegram")
    assert answer.done and "may sleep" in answer.text
    assert env.registry.hosts()[0].config.may_sleep is True


async def test_a_waiting_plan_is_rejected_first_through_the_same_decision(tmp_path):
    env = await graph_env(tmp_path)
    incident = await opened(env, death(shop(env), 0))
    approvals = env.deps.approvals
    async with open_checkpointer(tmp_path / "c.sqlite") as saver:
        rig = Rig(env, IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus))
        await approvals.request(incident.id, PLAN, [])
        assert (await rig.expectations.expected(incident.id, "telegram")).done
    record = await approvals.current(incident.id)
    assert record.decision.kind == "reject" and record.decision.via == "telegram"


async def test_a_plan_decided_elsewhere_first_wins_and_nothing_is_recorded(tmp_path):
    env = await graph_env(tmp_path)
    incident = await opened(env, death(shop(env), 0))
    approvals = env.deps.approvals
    async with open_checkpointer(tmp_path / "c.sqlite") as saver:
        rig = Rig(env, IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus))
        request = await approvals.request(incident.id, PLAN, [])
        yes = Decision(kind="approve", via="dashboard", at=datetime(2026, 11, 5, tzinfo=UTC))
        await approvals.resolve(incident.id, yes, request.id)
        answer = await rig.expectations.expected(incident.id, "telegram")

    assert not answer.done  # an approved plan runs, or is about to: it is not offered now
    assert env.registry.service(shop(env).service_id).exits == []
    assert (await env.repo.get(incident.id)).status != "closed"


async def test_nothing_is_offered_while_a_plan_runs_on_the_host(tmp_path):
    env = await graph_env(tmp_path)
    incident = await opened(env, death(shop(env), 0))
    await env.repo.set_status(incident.id, "executing")
    async with open_checkpointer(tmp_path / "c.sqlite") as saver:
        rig = Rig(env, IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus))
        assert await rig.expectations.offered(incident.id) is None
        assert not (await rig.expectations.expected(incident.id, "dashboard")).done


class YesFirst:
    """Approvals where the dashboard's yes lands just before "This is expected" resolves."""

    def __init__(self, real) -> None:
        self.real = real

    async def current(self, incident_id):
        return await self.real.current(incident_id)

    async def resolve(self, incident_id, decision, approval_id=None):
        yes = Decision(kind="approve", via="dashboard", at=datetime(2026, 11, 5, tzinfo=UTC))
        await self.real.resolve(incident_id, yes, approval_id)
        return await self.real.resolve(incident_id, decision, approval_id)


async def test_a_yes_that_lands_first_wins_the_race_and_nothing_is_recorded(tmp_path):
    env = await graph_env(tmp_path)
    incident = await opened(env, death(shop(env), 0))
    await env.deps.approvals.request(incident.id, PLAN, [])
    env.deps.approvals = YesFirst(env.deps.approvals)
    async with open_checkpointer(tmp_path / "c.sqlite") as saver:
        rig = Rig(env, IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus))
        answer = await rig.expectations.expected(incident.id, "telegram")

    assert answer == Answer(False, "Already decided on the dashboard; nothing was recorded.")
    assert env.registry.service(shop(env).service_id).exits == []
