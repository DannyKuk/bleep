"""The topology: which node follows which, with a scripted Propose."""

from datetime import UTC, datetime

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from bleep.graph.build import build_graph
from bleep.graph.nodes import propose
from bleep.graph.state import checkpoint_serializer
from bleep.incidents.runner import IncidentRunner
from bleep.schemas.graph import Decision, PlanStep, Proposal, VerificationPlan
from bleep.schemas.incidents import Subject
from graph_env import graph_env


def decide(kind: str, text: str | None = None) -> Decision:
    return Decision(kind=kind, text=text, via="telegram", at=datetime(2026, 11, 2, tzinfo=UTC))


@pytest.fixture
def seen_instructions(monkeypatch) -> list[list[str]]:
    """Propose returns a restart and records the instructions it was given."""
    seen: list[list[str]] = []

    def make(deps):
        async def node(state):
            seen.append(list(state.get("instructions", [])))
            return {
                "proposal": Proposal(
                    kind="act",
                    steps=[PlanStep(action="docker.restart", host_id="a", target="shop-web-1")],
                    reason="it exited",
                    verification=VerificationPlan(),
                )
            }

        return node

    monkeypatch.setattr(propose, "make", make)
    return seen


async def setup(tmp_path):
    env = await graph_env(tmp_path)
    subject = Subject(kind="service", host_id="a", service_id=env.service_id("shop"))
    incident = await env.open(subject, container="shop-web-1")
    runner = IncidentRunner(
        build_graph(env.deps, InMemorySaver(serde=checkpoint_serializer())), env.repo, env.bus
    )
    await runner.start(incident)
    await runner.join()
    return env, runner, incident


async def outcome(runner, incident_id):
    return (await runner.graph.aget_state(runner.config(incident_id))).values.get("outcome")


async def test_no_action_skips_approval(tmp_path):
    env, runner, incident = await setup(tmp_path)

    assert "approval" not in await env.nodes(incident.id)
    assert await outcome(runner, incident.id) == "no_action"


async def test_instruct_goes_back_to_propose_with_the_text(tmp_path, seen_instructions):
    env, runner, incident = await setup(tmp_path)

    await runner.resume(
        incident.id, decide("instruct", "redeploy instead"), await runner.waiting_for(incident.id)
    )
    await runner.join()

    assert seen_instructions == [[], ["redeploy instead"]]
    assert (await env.nodes(incident.id))[-3:] == ["approval", "propose", "approval"]
    assert (await env.repo.get(incident.id)).status == "awaiting_approval"


async def test_approve_executes_verifies_and_reports(tmp_path, seen_instructions):
    env, runner, incident = await setup(tmp_path)

    await runner.resume(incident.id, decide("approve"), await runner.waiting_for(incident.id))
    await runner.join()

    assert await outcome(runner, incident.id) == "resolved"
    assert [a.action for a in env.engine.acts] == ["restart"]
    finished = await env.nodes(incident.id, "node_finished")
    assert finished[-4:] == ["approval", "execute", "verify", "report"]
    assert (await env.repo.get(incident.id)).status == "resolved"


async def test_a_target_recreated_before_the_yes_is_planned_and_asked_again(
    tmp_path, seen_instructions
):
    env, runner, incident = await setup(tmp_path)
    env.engine.container_list[0] = env.engine.container_list[0].model_copy(update={"id": "new"})
    await env.registry.refresh("a")

    await runner.resume(incident.id, decide("approve"), await runner.waiting_for(incident.id))
    await runner.join()

    assert env.engine.acts == []
    assert (await env.nodes(incident.id))[-4:] == ["approval", "execute", "propose", "approval"]
    notices = [e.payload["text"] for e in await env.bus.history(incident.id) if e.kind == "notice"]
    assert any("state changed since approval" in n for n in notices)
    assert (await env.repo.get(incident.id)).status == "awaiting_approval"


async def test_instructions_stop_after_the_replan_budget(tmp_path, seen_instructions):
    env, runner, incident = await setup(tmp_path)
    for n in range(4):
        text = f"try {n}"
        await runner.resume(
            incident.id, decide("instruct", text), await runner.waiting_for(incident.id)
        )
        await runner.join()

    assert len(seen_instructions) == 4  # the first plan and three new ones
    assert await outcome(runner, incident.id) == "replan_limit"
    assert (await env.repo.get(incident.id)).status == "closed"
    assert env.engine.acts == []


async def test_reject_reports_rejected(tmp_path, seen_instructions):
    env, runner, incident = await setup(tmp_path)

    await runner.resume(incident.id, decide("reject"), await runner.waiting_for(incident.id))
    await runner.join()

    assert await outcome(runner, incident.id) == "rejected"
