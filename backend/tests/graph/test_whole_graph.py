"""The real nodes end to end: gather to approval, then a decision."""

import json
from datetime import UTC, datetime

import pytest
from langchain_core.messages import AIMessage

from bleep.graph.build import build_graph, open_checkpointer
from bleep.incidents.approvals import ApprovalStore
from bleep.incidents.runner import IncidentRunner
from bleep.schemas.graph import Decision
from bleep.schemas.incidents import Subject
from fakes import FakeChatModel
from graph_env import graph_env

AT = datetime(2026, 11, 2, 12, 30, tzinfo=UTC)
DIAGNOSIS = {"root_cause": "shop-web exited", "category": "unknown", "confidence": 0.9}


def plan(target: str) -> AIMessage:
    step = {"action": "docker.restart", "host_id": "a", "target": target}
    body = {"kind": "act", "steps": [step], "reason": "restart it", "verification": {}}
    return AIMessage(json.dumps(body))


@pytest.mark.parametrize("kind", ["approve", "reject", "instruct"])
async def test_a_run_goes_from_gather_to_the_interrupt_and_resumes_on_each_decision(tmp_path, kind):
    env = await graph_env(tmp_path)
    script = [
        AIMessage("nothing more to read"),
        AIMessage(json.dumps(DIAGNOSIS)),
        plan("shop-web-1"),
    ]
    if kind == "instruct":
        script.append(plan("shop-db-1"))  # Propose runs again with the instruction
    env.models.model = FakeChatModel(script=script, structured_output_unsupported=True)
    store = ApprovalStore(env.db, "test-secret")
    env.deps.approvals = store
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, store)
        subject = Subject(kind="service", host_id="a", service_id=env.service_id("shop"))
        incident = await env.open(subject, container="shop-web-1")

        await runner.start(incident)
        await runner.join()

        assert await env.nodes(incident.id) == [
            "gather", "investigate", "diagnose", "propose", "approval"
        ]  # fmt: skip
        record = await store.current(incident.id)
        assert record is not None and record.decision is None
        assert (await env.repo.get(incident.id)).status == "awaiting_approval"

        text = "The db instead: restart shop-db-1." if kind == "instruct" else None
        decision = Decision(kind=kind, text=text, via="dashboard", at=AT)
        assert await store.resolve(incident.id, decision) == decision
        await runner.resume(incident.id, decision, record.id)
        await runner.join()

        state = (await runner.graph.aget_state(runner.config(incident.id))).values
        if kind == "approve":
            assert state["grant"] is not None and state["outcome"] == "resolved"
            assert [a.action for a in env.engine.acts] == ["restart"]
            assert (await env.repo.get(incident.id)).status == "resolved"
        elif kind == "reject":
            assert state["outcome"] == "rejected"
            assert (await env.repo.get(incident.id)).status == "closed"
        else:
            assert state["instructions"] == ["The db instead: restart shop-db-1."]
            assert (await env.nodes(incident.id))[-2:] == ["propose", "approval"]
            assert (await store.current(incident.id)).id != record.id  # a new question
            assert (await env.repo.get(incident.id)).status == "awaiting_approval"


async def test_an_unsure_killed_container_reaches_the_approval_with_a_start(tmp_path):
    """The first live run: exit 137, no restart policy, confidence 0.45 twice, then a start."""
    from fakes import FakeEngine
    from graph_env import box

    engine = FakeEngine("a", [box("bleep-try", state="exited", exit_code=137, restart_policy="no")])
    env = await graph_env(tmp_path, engine=engine)
    unsure = AIMessage(json.dumps(DIAGNOSIS | {"confidence": 0.45, "root_cause": "killed"}))
    start = {"action": "docker.start", "host_id": "a", "target": "bleep-try"}
    body = {"kind": "act", "steps": [start], "reason": "I am not sure who killed it; start it.",
            "verification": {}}  # fmt: skip
    env.models.model = FakeChatModel(
        script=[
            AIMessage("nothing to read"), unsure, AIMessage("nothing more"), unsure,
            AIMessage(json.dumps(body)),
        ],
        structured_output_unsupported=True,
    )  # fmt: skip
    store = ApprovalStore(env.db, "test-secret")
    env.deps.approvals = store
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, store)
        subject = Subject(kind="service", host_id="a", service_id=env.service_id("bleep-try"))
        incident = await env.open(subject, container="bleep-try")

        await runner.start(incident)
        await runner.join()

        record = await store.current(incident.id)
        assert record is not None and record.decision is None
        assert (await env.repo.get(incident.id)).status == "awaiting_approval"
        state = (await runner.graph.aget_state(runner.config(incident.id))).values
        assert state["diagnosis"].unsure
        assert [s.action for s in state["proposal"].steps] == ["docker.start"]


def record_statuses(env) -> list[str]:
    """Every status the incident is given, in order, repeats collapsed."""
    seen: list[str] = []
    set_status = env.repo.set_status

    async def recording(incident_id: str, status: str) -> None:
        if not seen or seen[-1] != status:
            seen.append(status)
        await set_status(incident_id, status)

    env.repo.set_status = recording  # type: ignore[method-assign]
    return seen


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (
            "approve",
            ["investigating", "awaiting_approval", "executing", "verifying", "resolved"],
        ),
        (
            "instruct",
            ["investigating", "awaiting_approval", "investigating", "awaiting_approval"],
        ),
        ("reject", ["investigating", "awaiting_approval", "investigating", "closed"]),
    ],
)
async def test_the_status_follows_what_the_run_is_doing(tmp_path, kind, expected):
    env = await graph_env(tmp_path)
    script = [
        AIMessage("nothing more to read"),
        AIMessage(json.dumps(DIAGNOSIS)),
        plan("shop-web-1"),
    ]
    if kind == "instruct":
        script.append(plan("shop-db-1"))
    env.models.model = FakeChatModel(script=script, structured_output_unsupported=True)
    store = ApprovalStore(env.db, "test-secret")
    env.deps.approvals = store
    statuses = record_statuses(env)
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, store)
        subject = Subject(kind="service", host_id="a", service_id=env.service_id("shop"))
        incident = await env.open(subject, container="shop-web-1")
        await runner.start(incident)
        await runner.join()
        record = await store.current(incident.id)
        text = "The db instead: restart shop-db-1." if kind == "instruct" else None
        decision = Decision(kind=kind, text=text, via="dashboard", at=AT)
        await store.resolve(incident.id, decision)
        await runner.resume(incident.id, decision, record.id)
        await runner.join()

    assert statuses == expected


async def test_the_status_is_set_before_the_node_started_event_reaches_the_dashboard(tmp_path):
    env = await graph_env(tmp_path)
    env.models.model = FakeChatModel(
        script=[
            AIMessage("nothing more to read"),
            AIMessage(json.dumps(DIAGNOSIS)),
            plan("shop-web-1"),
        ],
        structured_output_unsupported=True,
    )
    store = ApprovalStore(env.db, "test-secret")
    env.deps.approvals = store
    seen: dict[str, str] = {}
    publish = env.bus.publish

    async def spying(incident_id, kind, payload=None):
        if kind == "node_started":
            seen[payload["node"]] = (await env.repo.get(incident_id)).status
        return await publish(incident_id, kind, payload)

    env.bus.publish = spying  # type: ignore[method-assign]
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, store)
        subject = Subject(kind="service", host_id="a", service_id=env.service_id("shop"))
        incident = await env.open(subject, container="shop-web-1")
        await runner.start(incident)
        await runner.join()
        record = await store.current(incident.id)
        decision = Decision(kind="approve", via="dashboard", at=AT)
        await store.resolve(incident.id, decision)
        await runner.resume(incident.id, decision, record.id)
        await runner.join()

    assert seen["execute"] == "executing" and seen["verify"] == "verifying"
