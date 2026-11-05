""" "This is expected": what it records, when it is offered, and how it races a decision."""

from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from bleep.graph.build import build_graph, open_checkpointer
from bleep.incidents.expectations import CLOSED_NOTICE, Answer, Expectations, expectation_for
from bleep.incidents.runner import IncidentRunner
from bleep.incidents.settings_store import SettingsRefused, SettingsStore
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


async def test_a_yes_that_lands_first_wins_the_plan_and_the_incident_stays_open(tmp_path):
    env = await graph_env(tmp_path)
    incident = await opened(env, death(shop(env), 0))
    await env.deps.approvals.request(incident.id, PLAN, [])
    env.deps.approvals = YesFirst(env.deps.approvals)
    async with open_checkpointer(tmp_path / "c.sqlite") as saver:
        rig = Rig(env, IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus))
        answer = await rig.expectations.expected(incident.id, "telegram")

    assert not answer.done
    assert answer.text.endswith(
        "The plan was already decided on the dashboard, so the incident stays open."
    )
    assert env.registry.service(shop(env).service_id).exits == ["shop-web-1"]  # written first
    assert (await env.repo.get(incident.id)).status != "closed"


async def test_a_write_that_fails_changes_nothing_and_the_plan_still_waits(tmp_path):
    env = await graph_env(tmp_path)
    incident = await opened(env, death(shop(env), 0))
    request = await env.deps.approvals.request(incident.id, PLAN, [])
    async with open_checkpointer(tmp_path / "c.sqlite") as saver:
        rig = Rig(env, IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus))

        async def refuse(*args):
            raise SettingsRefused("the database is read-only")

        rig.settings.expect_exits = refuse  # type: ignore[method-assign]
        answer = await rig.expectations.expected(incident.id, "telegram")

    assert answer == Answer(False, "Bleep could not record that; nothing was changed.")
    record = await env.deps.approvals.current(incident.id)
    assert record.id == request.id and record.decision is None
    assert (await env.repo.get(incident.id)).status == "open"


@pytest.mark.parametrize(
    ("label", "entry"),
    [("fetch", "fetch"), ("*", "shop-web-1"), ("x" * 300, "shop-web-1"), ("a\nb", "shop-web-1")],
)
async def test_a_label_that_is_no_name_falls_back_to_the_container_name(tmp_path, label, entry):
    env = await graph_env(tmp_path)
    incident = await opened(env, death(shop(env), 0, compose_service=label))
    offered = expectation_for(incident, await env.repo.signals(incident.id), env.registry)
    assert offered is not None and offered.entries == (entry,)


async def test_nothing_is_offered_when_no_name_can_hold_the_job(tmp_path):
    env = await graph_env(tmp_path)
    odd = Signal(
        subject=shop(env),
        kind="container_died",
        at=T0,
        container="-odd name",
        detail={"exit_code": 0, "compose_service": "*"},
    )
    incident = await opened(env, odd)
    assert expectation_for(incident, await env.repo.signals(incident.id), env.registry) is None


def yes_during_the_write(
    env, rig, incident_id: str, status: str | None = None, kind: str = "approve"
) -> None:
    """A decision on the dashboard (a yes by default) wins the plan while "This is expected"
    writes the settings, and the run may reach `status` before the write returns."""
    write = rig.settings.expect_exits

    async def racing(service, entries):
        current = await env.deps.approvals.current(incident_id)
        text = "use the other port" if kind == "instruct" else None
        yes = Decision(kind=kind, text=text, via="dashboard", at=datetime(2026, 11, 5, tzinfo=UTC))
        await env.deps.approvals.resolve(incident_id, yes, current.id)
        if status is not None:
            await env.repo.set_status(incident_id, status)
        await write(service, entries)

    rig.settings.expect_exits = racing  # type: ignore[method-assign]


@pytest.mark.parametrize("status", ["awaiting_approval", "executing"])
async def test_a_yes_that_lands_during_the_write_wins_and_the_incident_stays_open(tmp_path, status):
    env = await graph_env(tmp_path)
    incident = await opened(env, death(shop(env), 0))
    await env.repo.set_status(incident.id, "awaiting_approval")
    await env.deps.approvals.request(incident.id, PLAN, [])
    async with open_checkpointer(tmp_path / "c.sqlite") as saver:
        rig = Rig(env, IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus))
        yes_during_the_write(env, rig, incident.id, status)
        answer = await rig.expectations.expected(incident.id, "telegram")

    assert not answer.done
    assert answer.text.endswith(
        "The plan was already decided on the dashboard, so the incident stays open."
    )
    assert (await env.repo.get(incident.id)).status == status  # the approved plan goes on
    assert (await env.deps.approvals.current(incident.id)).decision.kind == "approve"
    events = [e for e in await env.bus.history(incident.id) if e.kind == "expected"]
    assert events[-1].payload["via"] == "telegram"


@pytest.mark.parametrize("kind", ["instruct", "reject"])
async def test_an_instruction_or_no_that_lands_during_the_write_wins_too(tmp_path, kind):
    env = await graph_env(tmp_path)
    incident = await opened(env, death(shop(env), 0))
    await env.repo.set_status(incident.id, "awaiting_approval")
    await env.deps.approvals.request(incident.id, PLAN, [])
    async with open_checkpointer(tmp_path / "c.sqlite") as saver:
        rig = Rig(env, IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus))
        yes_during_the_write(env, rig, incident.id, kind=kind)
        answer = await rig.expectations.expected(incident.id, "telegram")

    assert not answer.done
    assert answer.text.endswith(
        "The plan was already decided on the dashboard, so the incident stays open."
    )
    assert (await env.repo.get(incident.id)).status == "awaiting_approval"
    assert (await env.deps.approvals.current(incident.id)).decision.kind == kind


async def test_a_close_from_outside_during_the_write_is_told_as_a_close_not_a_decision(tmp_path):
    """Leave alone closes the incident while "This is expected" writes the settings (#277): the
    plan was never decided, so the answer must not say it was or that the incident stays open."""
    env = await graph_env(tmp_path)
    incident = await opened(env, death(shop(env), 0))
    await env.repo.set_status(incident.id, "awaiting_approval")
    await env.deps.approvals.request(incident.id, PLAN, [])
    async with open_checkpointer(tmp_path / "c.sqlite") as saver:
        rig = Rig(env, IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus))
        write = rig.settings.expect_exits

        async def closed_meanwhile(service, entries):
            await env.repo.close_unfinished(incident.id)
            await write(service, entries)

        rig.settings.expect_exits = closed_meanwhile  # type: ignore[method-assign]
        answer = await rig.expectations.expected(incident.id, "dashboard")

    assert not answer.done
    assert answer.text.endswith(
        "The incident was closed meanwhile, before anyone answered its plan."
    )
    assert "stays open" not in answer.text
    assert (await env.repo.get(incident.id)).status == "closed"
    record = await env.deps.approvals.current(incident.id)
    assert record.closed and record.decision is None


async def test_it_is_not_done_when_a_plan_started_running_and_the_close_was_spared(tmp_path):
    env = await graph_env(tmp_path)
    incident = await opened(env, death(shop(env), 0))
    async with open_checkpointer(tmp_path / "c.sqlite") as saver:
        rig = Rig(env, IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus))
        write = rig.settings.expect_exits

        async def then_executing(service, entries):
            await write(service, entries)
            await env.repo.set_status(incident.id, "executing")

        rig.settings.expect_exits = then_executing  # type: ignore[method-assign]
        answer = await rig.expectations.expected(incident.id, "dashboard")

    assert not answer.done and answer.text.endswith(
        "The incident was not closed here; see its page."
    )
    assert (await env.repo.get(incident.id)).status == "executing"


async def test_a_crash_after_this_is_expected_is_a_repeat_that_still_reports(tmp_path):
    """The job's exit 0 was marked expected; then the same container crashes. The crash joins
    the group, so it is not announced as new, but what came of its run reaches the channels."""
    env = await graph_env(tmp_path)
    reports: list[str] = []

    class Reports:
        async def report(self, incident, report) -> None:
            reports.append(report.title)

        def __getattr__(self, name):
            async def nothing(*args, **kwargs) -> None:
                return None

            return nothing

    env.deps.notifier = Reports()
    expected = await opened(env, death(shop(env), 0))
    async with open_checkpointer(tmp_path / "c.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus)
        rig = Rig(env, runner)
        assert (await rig.expectations.expected(expected.id, "telegram")).done
        crash = await opened(env, death(shop(env), 137))
        assert crash.fold_of == expected.id
        await runner.start(crash)
        await runner.join()

    assert len(reports) == 1 and "(seen 2 times since" in reports[0]
