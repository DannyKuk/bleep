"""The Milestone 2 topology: which node follows which, with the placeholder nodes."""

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

    await runner.resume(incident.id, decide("instruct", "redeploy instead"))
    await runner.join()

    assert seen_instructions == [[], ["redeploy instead"]]
    assert (await env.nodes(incident.id))[-3:] == ["approval", "propose", "approval"]
    assert (await env.repo.get(incident.id)).status == "awaiting_approval"


async def test_approve_reports_unverified_until_execute_exists(tmp_path, seen_instructions):
    env, runner, incident = await setup(tmp_path)

    await runner.resume(incident.id, decide("approve"))
    await runner.join()

    # Milestone 2 has no Execute: an approved plan is reported, never claimed as fixed.
    assert await outcome(runner, incident.id) == "unverified"
    assert (await env.nodes(incident.id, "node_finished"))[-2:] == ["approval", "report"]
    assert (await env.repo.get(incident.id)).status == "closed"


async def test_reject_reports_rejected(tmp_path, seen_instructions):
    env, runner, incident = await setup(tmp_path)

    await runner.resume(incident.id, decide("reject"))
    await runner.join()

    assert await outcome(runner, incident.id) == "rejected"
