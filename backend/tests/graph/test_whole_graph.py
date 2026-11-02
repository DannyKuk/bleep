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

        decision = Decision(kind=kind, text="the db instead" if kind == "instruct" else None,
                            via="dashboard", at=AT)  # fmt: skip
        assert await store.resolve(incident.id, decision) == decision
        await runner.resume(incident.id, decision, record.id)
        await runner.join()

        state = (await runner.graph.aget_state(runner.config(incident.id))).values
        if kind == "approve":
            assert state["grant"] is not None and state["outcome"] == "unverified"
            assert (await env.repo.get(incident.id)).status == "closed"
        elif kind == "reject":
            assert state["outcome"] == "rejected"
            assert (await env.repo.get(incident.id)).status == "closed"
        else:
            assert state["instructions"] == ["the db instead"]
            assert (await env.nodes(incident.id))[-2:] == ["propose", "approval"]
            assert (await store.current(incident.id)).id != record.id  # a new question
            assert (await env.repo.get(incident.id)).status == "awaiting_approval"
