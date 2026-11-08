import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from langchain_core.tools import BaseTool

from bleep.guard.grant import GrantInvalid, grant_token, plan_hash
from bleep.hosts.config import ServiceOverrides
from bleep.schemas.graph import (
    ApprovalGrant,
    ApprovalRecord,
    PlanStep,
    Proposal,
    StepResult,
    TargetSnapshot,
)
from bleep.tools.default import default_registry
from bleep.tools.docker_act import StepRefused, docker_act_tools
from bleep.tools.registry import ToolContext
from fakes import FakeEngine
from graph_env import RecordingGuardLog, box, graph_env, shop_host

SECRET = "test-secret"
T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
T1 = datetime(2026, 11, 2, 12, 5, tzinfo=UTC)


def step(target: str = "shop-web-1", action: str = "docker.restart") -> PlanStep:
    return PlanStep(action=action, host_id="a", target=target)  # type: ignore[arg-type]


def plan(*steps: PlanStep, incident_id: str = "inc_1") -> ApprovalRecord:
    """An approved plan as the store holds it, each target snapshotted at its current id."""
    steps = steps or (step(),)
    proposal = Proposal(kind="act", steps=list(steps), reason="it exited")
    snapshots = [
        TargetSnapshot(host_id="a", target=s.target, container_id=f"id-{s.target}") for s in steps
    ]
    return ApprovalRecord(
        id="ap_1",
        incident_id=incident_id,
        plan_hash=plan_hash(proposal, snapshots),
        proposal=proposal,
        snapshots=snapshots,
        requested_at=T0,
    )


def grant_for(record: ApprovalRecord, secret: str = SECRET) -> ApprovalGrant:
    token = grant_token(secret, record.incident_id, record.plan_hash)
    return ApprovalGrant(incident_id=record.incident_id, plan_hash=record.plan_hash, token=token)


def guarded_engine() -> FakeEngine:
    return FakeEngine(
        "a",
        [
            box("shop-web-1", "shop", state="exited", exit_code=1),
            box("shop-db-1", "shop"),
            box("bleep-backend-1", "bleep", labels={"bleep.self": "true"}),
        ],
        clock=lambda: T1,
    )


class Env:
    def __init__(self, env, guard_log: RecordingGuardLog) -> None:
        self.registry = env.registry
        self.engine: FakeEngine = env.engine
        self.guard_log = guard_log
        self.tools = docker_act_tools(SECRET, guard_log, clock=lambda: T1)

    async def run(
        self,
        record: ApprovalRecord,
        grant: ApprovalGrant | None,
        index: int = 0,
        the_step: PlanStep | None = None,
    ) -> StepResult:
        the_step = the_step or record.proposal.steps[index]
        tool = self.tools[the_step.action]
        return await tool.run(the_step, grant, self.registry, plan=record, step_index=index)


@pytest.fixture
async def env(tmp_path) -> Env:
    host = shop_host(services={"shop": ServiceOverrides(protected_containers=["shop-db-1"])})
    built = await graph_env(tmp_path, engine=guarded_engine(), host=host)
    return Env(built, RecordingGuardLog())


async def test_a_valid_grant_restarts_the_container_exactly_once(env):
    record = plan()

    result = await env.run(record, grant_for(record))

    assert [(a.action, a.container) for a in env.engine.acts] == [("restart", "shop-web-1")]
    assert result == StepResult(
        step_index=0,
        action="docker.restart",
        target="shop-web-1",
        ok=True,
        detail="restarted shop-web-1",
        started_at=T1,
        finished_at=T1,
    )
    assert env.guard_log.records == []


@pytest.mark.parametrize(
    ("action", "verb", "detail"),
    [
        ("docker.stop", "stop", "stopped shop-web-1"),
        ("docker.start", "start", "started shop-web-1"),
    ],
)
async def test_stop_and_start_call_their_own_engine_method(env, action, verb, detail):
    record = plan(step(action=action))

    result = await env.run(record, grant_for(record))

    assert [a.action for a in env.engine.acts] == [verb]
    assert result.ok and result.detail == detail


async def test_the_engine_is_called_with_the_container_id_the_human_approved(env, monkeypatch):
    called: list[str] = []

    async def restart(container: str) -> None:
        called.append(container)

    monkeypatch.setattr(env.engine, "restart", restart)
    record = plan()

    await env.run(record, grant_for(record))

    assert called == ["id-shop-web-1"]


async def test_no_grant_is_refused_and_the_engine_is_untouched(env):
    with pytest.raises(GrantInvalid, match="no grant"):
        await env.run(plan(), None)

    assert env.engine.acts == []
    assert env.guard_log.records == [
        (
            "inc_1",
            "blocked_action",
            "step 1 (docker.restart shop-web-1 on a): refused, no grant",
        )
    ]


async def test_a_grant_for_plan_a_used_on_plan_b_is_refused(env):
    plan_a, plan_b = plan(step("shop-web-1")), plan(step("shop-web-1", "docker.stop"))

    with pytest.raises(GrantInvalid, match="another plan"):
        await env.run(plan_b, grant_for(plan_a))

    assert env.engine.acts == []


async def test_a_grant_for_another_incident_is_refused(env):
    other = plan(incident_id="inc_2")

    with pytest.raises(GrantInvalid, match="another incident"):
        await env.run(plan(), grant_for(other))

    assert env.engine.acts == []


async def test_a_grant_signed_with_another_secret_is_refused(env):
    record = plan()

    with pytest.raises(GrantInvalid, match="token"):
        await env.run(record, grant_for(record, secret="not-the-secret"))

    assert env.engine.acts == []


async def test_a_plan_edited_after_approval_no_longer_matches_its_grant(env):
    # The stored hash still says plan A, but a step was swapped: the hash is recomputed, not read.
    record = plan()
    grant = grant_for(record)
    edited = record.model_copy(
        update={"proposal": record.proposal.model_copy(update={"steps": [step("shop-db-1")]})}
    )

    with pytest.raises(GrantInvalid, match="another plan"):
        await env.run(edited, grant)

    assert env.engine.acts == []


async def test_a_step_outside_the_granted_plan_is_refused(env):
    record = plan()
    grant = grant_for(record)

    with pytest.raises(GrantInvalid, match="not step 1 of the approved plan"):
        await env.run(record, grant, the_step=step("shop-web-1", "docker.stop"))
    with pytest.raises(GrantInvalid, match="no step 2"):
        await env.run(record, grant, index=1, the_step=step())

    assert env.engine.acts == []


async def test_a_tool_refuses_a_step_of_another_action(env):
    record = plan()

    with pytest.raises(StepRefused, match="docker.stop cannot run docker.restart"):
        await env.tools["docker.stop"].run(
            record.proposal.steps[0], grant_for(record), env.registry, plan=record, step_index=0
        )

    assert env.engine.acts == []


async def test_a_protected_container_is_refused_even_with_a_valid_grant(env):
    record = plan(step("shop-db-1"))

    with pytest.raises(StepRefused, match="protected"):
        await env.run(record, grant_for(record))

    assert env.engine.acts == []
    assert env.guard_log.records[0][1] == "blocked_action"
    assert "shop-db-1 is protected in the hosts file" in env.guard_log.records[0][2]


async def test_bleeps_own_container_is_refused_even_with_a_valid_grant(env):
    record = plan(step("bleep-backend-1"))

    with pytest.raises(StepRefused, match="one of Bleep's own containers"):
        await env.run(record, grant_for(record))

    assert env.engine.acts == []


async def test_a_snapshot_id_of_bleeps_own_container_is_refused(env):
    # The step names a plain container, but the id the plan would act on is Bleep's own.
    record = plan()
    record.snapshots[0] = record.snapshots[0].model_copy(
        update={"container_id": "id-bleep-backend-1"}
    )
    record = record.model_copy(update={"plan_hash": plan_hash(record.proposal, record.snapshots)})

    with pytest.raises(StepRefused, match="one of Bleep's own containers"):
        await env.run(record, grant_for(record))

    assert env.engine.acts == []


async def test_a_plan_with_no_snapshot_of_its_target_is_refused(env):
    record = plan()
    record = record.model_copy(
        update={"snapshots": [], "plan_hash": plan_hash(record.proposal, [])}
    )

    with pytest.raises(StepRefused, match="no snapshot"):
        await env.run(record, grant_for(record))

    assert env.engine.acts == []


async def test_any_target_on_a_host_with_a_config_error_is_refused_even_with_a_valid_grant(
    tmp_path,
):
    # Two Coolify apps share the display name "web", so the override is ambiguous and the
    # registry sets `HostState.error`: its protected list may not apply.
    def app(uuid: str) -> dict[str, str]:
        return {"coolify.type": "application", "coolify.applicationUuid": uuid,
                "coolify.resourceName": "web"}  # fmt: skip

    engine = FakeEngine("a", [box("web-a-1", labels=app("u1")), box("web-b-1", labels=app("u2"))])
    host = shop_host(services={"web": ServiceOverrides(protected_containers=["web-a-1"])})
    env = Env(await graph_env(tmp_path, engine=engine, host=host), RecordingGuardLog())
    assert env.registry.hosts()[0].error is not None
    record = plan(step("web-b-1"))

    with pytest.raises(StepRefused, match="configuration error"):
        await env.run(record, grant_for(record))

    assert engine.acts == []


async def test_a_target_on_an_unreachable_host_is_refused(env):
    env.engine.drop()
    await env.registry.refresh("a")
    record = plan()

    with pytest.raises(StepRefused, match="unreachable"):
        await env.run(record, grant_for(record))

    assert env.engine.acts == []


async def test_a_container_recreated_since_approval_fails_the_step_without_acting(env):
    # Same name, new id: the engine has no container with the approved id.
    index = next(i for i, c in enumerate(env.engine.container_list) if c.name == "shop-web-1")
    env.engine.container_list[index] = box("shop-web-1", "shop").model_copy(update={"id": "new"})
    record = plan()

    result = await env.run(record, grant_for(record))

    assert env.engine.acts == []
    assert not result.ok
    assert result.detail == "the container is gone; it was removed or recreated since approval"


async def test_an_engine_drop_during_the_call_fails_the_step(env):
    record = plan()
    grant = grant_for(record)
    env.engine.unavailable = True  # the registry still thinks the host is up

    result = await env.run(record, grant)

    assert not result.ok and result.detail == "host unreachable"
    assert env.engine.acts == []


async def test_act_tools_are_never_bound_to_the_model(env):
    tools = docker_act_tools(SECRET)
    assert set(tools) == {"docker.restart", "docker.stop", "docker.start"}
    assert not any(isinstance(t, BaseTool) for t in tools.values())

    registry = default_registry()
    assert [s.name for s in registry.specs() if s.kind == "act"] == []
    offered = {t.name for t in registry.read_tools(ToolContext("inc_1", "a", env.registry))}
    assert offered and offered.isdisjoint(tools)
    assert not any(name.startswith(("docker.", "restart", "stop", "start")) for name in offered)


def test_an_act_tool_never_prints_its_secret():
    assert SECRET not in repr(docker_act_tools(SECRET)["docker.restart"])


DB = "k8s0db1x-104512093422"  # Coolify names a container <uuid>-<deploy time>
DB_APP = {
    "coolify.type": "application",
    "coolify.applicationUuid": "k8s0db1x",
    "coolify.resourceName": "billing-db",
}


async def coolify_env(tmp_path, protected: str) -> Env:
    engine = FakeEngine(
        "a",
        [box(DB, labels=DB_APP, state="exited", exit_code=1), box("shop-web-1", "shop")],
        clock=lambda: T1,
    )
    host = shop_host(services={"billing": ServiceOverrides(protected_containers=[protected])})
    return Env(await graph_env(tmp_path, engine=engine, host=host), RecordingGuardLog())


@pytest.mark.parametrize("action", ["docker.restart", "docker.stop"])
@pytest.mark.parametrize("protected", ["k8s0db1x", "billing-db"])
async def test_a_protected_coolify_apps_container_is_refused_even_with_a_valid_grant(
    tmp_path, action, protected
):
    env = await coolify_env(tmp_path, protected)
    record = plan(step(DB, action))

    with pytest.raises(StepRefused, match="protected"):
        await env.run(record, grant_for(record))

    assert env.engine.acts == []
    [(_, kind, detail)] = env.guard_log.records
    assert kind == "blocked_action"
    assert f"{DB} belongs to the Coolify application {protected}" in detail


async def test_a_snapshot_id_of_a_protected_container_is_refused(tmp_path):
    # The step names a plain container, but the id the plan would act on is a protected one's.
    env = await coolify_env(tmp_path, "k8s0db1x")
    record = plan()
    record.snapshots[0] = record.snapshots[0].model_copy(update={"container_id": f"id-{DB}"})
    record = record.model_copy(update={"plan_hash": plan_hash(record.proposal, record.snapshots)})

    with pytest.raises(StepRefused, match="belongs to the Coolify application k8s0db1x"):
        await env.run(record, grant_for(record))

    assert env.engine.acts == []


async def test_a_coolify_4_1_2_container_protected_by_its_uuid_is_refused(tmp_path):
    # Captured from a real host: no coolify.applicationUuid, the uuid only in its names.
    data = json.loads(
        (
            Path(__file__).parent.parent
            / "fixtures"
            / "labels"
            / ("coolify-4.1.2-applicationid.json")
        ).read_text()
    )
    name = data["container"]
    engine = FakeEngine(
        "a", [box(name, labels=data["labels"], state="exited", exit_code=1)], clock=lambda: T1
    )
    host = shop_host(
        services={"x": ServiceOverrides(protected_containers=["cr2hzhk4nvabtad0zinjw1ev"])}
    )
    env = Env(await graph_env(tmp_path, engine=engine, host=host), RecordingGuardLog())
    record = plan(step(name, "docker.stop"))

    with pytest.raises(StepRefused, match="Coolify application cr2hzhk4nvabtad0zinjw1ev"):
        await env.run(record, grant_for(record))

    assert engine.acts == []
