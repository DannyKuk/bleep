from datetime import UTC, datetime

import pytest

from bleep.graph.build import build_graph, open_checkpointer
from bleep.graph.nodes import approval, propose
from bleep.incidents.approvals import ApprovalStore, grant_token
from bleep.incidents.runner import IncidentRunner
from bleep.schemas.graph import Decision, PlanStep, Proposal, VerificationPlan
from bleep.schemas.incidents import Subject
from graph_env import GraphEnv, graph_env

SECRET = "test-secret"
AT = datetime(2026, 11, 2, 12, 30, tzinfo=UTC)


def decide(kind: str, text: str | None = None, via: str = "dashboard") -> Decision:
    return Decision(kind=kind, text=text, via=via, at=AT)


def restart(target: str = "shop-web-1") -> Proposal:
    return Proposal(
        kind="act",
        steps=[PlanStep(action="docker.restart", host_id="a", target=target)],
        reason="it exited",
        verification=VerificationPlan(containers=[target]),
    )


class RecordingNotifier:
    def __init__(self) -> None:
        self.proposals: list[tuple[str, Proposal, str]] = []

    async def proposal(self, incident, diagnosis, proposal, approval) -> None:
        self.proposals.append((incident.id, proposal, approval.id))


@pytest.fixture
def plans(monkeypatch):
    """Propose returns the next plan of this list each time it runs (the last one repeats);
    the instructions it was given are recorded in `seen`."""
    script: list[Proposal] = []
    seen: list[list[str]] = []

    def make(deps):
        async def node(state):
            seen.append(list(state.get("instructions", [])))
            return {"proposal": script[min(len(seen), len(script)) - 1]}

        return node

    monkeypatch.setattr(propose, "make", make)
    return script, seen


class Run:
    """A graph environment with the SQLite approvals, a recording notifier and a runner."""

    def __init__(self, env: GraphEnv, saver) -> None:
        self.env = env
        self.store = ApprovalStore(env.db, SECRET)
        self.notifier = RecordingNotifier()
        env.deps.approvals = self.store
        env.deps.notifier = self.notifier  # type: ignore[assignment]
        self.runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, self.store)

    async def start(self) -> str:
        subject = Subject(kind="service", host_id="a", service_id=self.env.service_id("shop"))
        incident = await self.env.open(subject, container="shop-web-1")
        await self.runner.start(incident)
        await self.runner.join()
        return incident.id

    async def decide(self, incident_id: str, decision: Decision) -> None:
        """What the dashboard and Telegram do: win the approval, then resume the run."""
        assert await self.store.resolve(incident_id, decision) == decision
        await self.runner.resume(incident_id, decision)
        await self.runner.join()

    async def state(self, incident_id: str) -> dict:
        return (await self.runner.graph.aget_state(self.runner.config(incident_id))).values

    async def events(self, incident_id: str, kind: str) -> list[dict]:
        return [e.payload for e in await self.env.bus.history(incident_id) if e.kind == kind]


@pytest.fixture
async def run(tmp_path, plans):  # after `plans`, so the graph is built with the scripted Propose
    env = await graph_env(tmp_path)
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        yield Run(env, saver)


async def test_an_act_plan_asks_once_notifies_and_waits(run, plans):
    plans[0].append(restart())
    incident_id = await run.start()

    record = await run.store.current(incident_id)
    assert record is not None and record.decision is None
    assert [(s.target, s.container_id) for s in record.snapshots] == [
        ("shop-web-1", "id-shop-web-1")
    ]
    assert await run.events(incident_id, "approval_requested") == [
        {"approval_id": record.id, "plan_hash": record.plan_hash}
    ]
    assert run.notifier.proposals == [(incident_id, restart(), record.id)]
    assert (await run.env.repo.get(incident_id)).status == "awaiting_approval"


async def test_approve_carries_a_grant_for_the_approved_plan(run, plans):
    plans[0].append(restart())
    incident_id = await run.start()
    await run.decide(incident_id, decide("approve", via="telegram"))

    state = await run.state(incident_id)
    record = await run.store.current(incident_id)
    assert state["decision"].kind == "approve"
    assert state["grant"].plan_hash == record.plan_hash
    assert state["grant"].token == grant_token(SECRET, incident_id, record.plan_hash)
    assert state["outcome"] == "unverified"  # nothing ran yet: never "resolved"
    assert await run.events(incident_id, "decision") == [
        {"kind": "approve", "text": None, "via": "telegram"}
    ]
    assert len(run.notifier.proposals) == 1  # resuming did not ask again
    assert len(await run.events(incident_id, "approval_requested")) == 1


async def test_reject_closes_with_outcome_rejected(run, plans):
    plans[0].append(restart())
    incident_id = await run.start()
    await run.decide(incident_id, decide("reject"))

    state = await run.state(incident_id)
    assert state["outcome"] == "rejected"
    assert state.get("grant") is None
    assert (await run.env.repo.get(incident_id)).status == "closed"
    assert (await run.env.nodes(incident_id))[-2:] == ["approval", "report"]


async def test_an_instruction_goes_back_to_propose_with_the_text(run, plans):
    plans[0].extend([restart(), restart("shop-db-1")])
    seen = plans[1]
    incident_id = await run.start()
    first = await run.store.current(incident_id)
    await run.decide(incident_id, decide("instruct", "restart the db instead"))

    assert seen == [[], ["restart the db instead"]]
    state = await run.state(incident_id)
    assert state["instructions"] == ["restart the db instead"]
    second = await run.store.current(incident_id)
    assert second.id != first.id and second.decision is None  # a new question for the new plan
    assert second.snapshots[0].target == "shop-db-1"
    assert len(run.notifier.proposals) == 2
    assert (await run.env.repo.get(incident_id)).status == "awaiting_approval"

    await run.decide(incident_id, decide("reject"))
    assert (await run.state(incident_id))["outcome"] == "rejected"


async def test_no_action_skips_approval(run, plans):
    plans[0].append(Proposal(kind="no_action", reason="the env var needs a human"))
    incident_id = await run.start()

    assert "approval" not in await run.env.nodes(incident_id)
    assert (await run.state(incident_id))["outcome"] == "no_action"
    assert await run.store.current(incident_id) is None
    assert run.notifier.proposals == []


async def test_the_stored_winner_beats_a_different_resume_value(run, plans):
    plans[0].append(restart())
    incident_id = await run.start()
    assert await run.store.resolve(incident_id, decide("reject", via="telegram")) is not None

    await run.runner.resume(incident_id, decide("approve"))  # a late loser resumes anyway
    await run.runner.join()

    state = await run.state(incident_id)
    assert state["decision"].kind == "reject"
    assert state.get("grant") is None
    assert state["outcome"] == "rejected"


async def test_a_failing_notifier_does_not_strand_the_approval(run, plans):
    async def down(*args) -> None:
        raise RuntimeError("telegram is down")

    run.notifier.proposal = down  # type: ignore[method-assign]
    plans[0].append(restart())
    incident_id = await run.start()

    assert (await run.env.repo.get(incident_id)).status == "awaiting_approval"
    assert any("channel" in n["text"] for n in await run.events(incident_id, "notice"))
    await run.decide(incident_id, decide("reject"))
    assert (await run.state(incident_id))["decision"].kind == "reject"


async def test_a_resume_nobody_recorded_is_recorded_first(run, plans):
    plans[0].append(restart())
    incident_id = await run.start()
    await run.runner.resume(incident_id, decide("approve"))
    await run.runner.join()

    record = await run.store.current(incident_id)
    assert record.decision == decide("approve")
    assert (await run.state(incident_id))["grant"] is not None


async def test_a_target_that_cannot_be_read_goes_back_to_propose(run, plans):
    plans[0].extend([restart("gone-1"), Proposal(kind="no_action", reason="nothing to do")])
    incident_id = await run.start()

    assert await run.store.current(incident_id) is None  # nothing was put to the human
    assert run.notifier.proposals == []
    notices = await run.events(incident_id, "notice")
    assert notices and "gone-1" in notices[-1]["text"]
    assert (await run.env.nodes(incident_id))[-3:] == ["approval", "propose", "report"]
    assert (await run.state(incident_id))["outcome"] == "no_action"


def test_route():
    act = restart()
    assert approval.route({"proposal": act, "decision": decide("approve")}) == "report"
    assert approval.route({"proposal": act, "decision": decide("reject")}) == "report"
    assert approval.route({"proposal": act, "decision": decide("instruct", "x")}) == "propose"
    assert approval.route({"proposal": act, "decision": None}) == "propose"
    no_action = Proposal(kind="no_action", reason="r")
    assert approval.route({"proposal": no_action, "decision": None}) == "report"
    assert approval.route({}) == "report"
