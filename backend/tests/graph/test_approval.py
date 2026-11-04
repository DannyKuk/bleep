from datetime import UTC, datetime

import pytest

from bleep.graph.build import build_graph, open_checkpointer
from bleep.graph.deps import StaticSettings
from bleep.graph.nodes import approval, propose
from bleep.incidents.approvals import ApprovalStore, grant_token
from bleep.incidents.runner import IncidentRunner
from bleep.schemas.graph import Decision, PlanStep, Proposal, VerificationPlan
from bleep.schemas.incidents import Subject
from bleep.schemas.settings import Budgets
from fakes import FakeEngine
from graph_env import GraphEnv, box, graph_env

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

    async def verification(self, incident, result) -> None:
        return None

    async def report(self, incident, report) -> None:
        return None


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
        record = await self.store.current(incident_id)
        assert await self.store.resolve(incident_id, decision, record.id) == decision
        await self.runner.resume(incident_id, decision, record.id)
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
    assert [(s.target, s.container_id, s.state, s.restart_count) for s in record.snapshots] == [
        ("shop-web-1", "id-shop-web-1", "exited", 0)
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
    # Execute restarted it and Verify saw it running; only that may say "resolved".
    assert state["outcome"] == "resolved"
    assert [r.ok for r in state["step_results"]] == [True]
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
    asked = await run.store.current(incident_id)
    assert await run.store.resolve(incident_id, decide("reject", via="telegram")) is not None

    await run.runner.resume(incident_id, decide("approve"), asked.id)  # a late loser resumes
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
    asked = await run.store.current(incident_id)
    await run.runner.resume(incident_id, decide("approve"), asked.id)
    await run.runner.join()

    record = await run.store.current(incident_id)
    assert record.decision == decide("approve")
    assert (await run.state(incident_id))["grant"] is not None


async def test_a_resume_the_store_neither_takes_nor_holds_fails_the_run_plainly(run, plans):
    """Not an `assert`: the check must hold under `python -O` too."""
    plans[0].append(restart())
    incident_id = await run.start()
    asked = await run.store.current(incident_id)

    async def refuse(*args) -> None:
        return None  # the store took nothing, and still holds the approval as pending

    run.store.resolve = refuse  # type: ignore[method-assign]
    await run.runner.resume(incident_id, decide("approve"), asked.id)
    await run.runner.join()

    assert (await run.env.repo.get(incident_id)).status == "failed"
    [notice] = [n for n in await run.events(incident_id, "notice") if "error" in n["text"]]
    assert "ApprovalNotDecided" in notice["text"]
    assert await run.events(incident_id, "decision") == []
    assert (await run.state(incident_id)).get("grant") is None


async def test_a_queued_resume_never_decides_the_next_plan(run, plans):
    """The human answers plan A on the dashboard while the node is still sending A to Telegram.
    The node finds A decided and goes on without interrupting; the dashboard's resume is queued
    behind the run and must not land on plan B, which nobody has seen."""
    plans[0].extend([restart(), restart("shop-db-1")])
    seen = plans[1]
    instruction = decide("instruct", "use the db")

    async def answered_while_sending(incident, diagnosis, proposal, approval) -> None:
        run.notifier.proposals.append((incident.id, proposal, approval.id))
        if len(run.notifier.proposals) == 1:  # exactly what api/incidents.decide does
            assert await run.store.resolve(incident.id, instruction, approval.id) == instruction
            await run.runner.resume(incident.id, instruction, approval.id)

    run.notifier.proposal = answered_while_sending  # type: ignore[method-assign]
    incident_id = await run.start()

    assert seen == [[], ["use the db"]]  # Propose ran twice: once per plan, never a third time
    [(_, _, a_id), (_, second, b_id)] = run.notifier.proposals
    assert second.steps[0].target == "shop-db-1"
    plan_b = await run.store.current(incident_id)
    assert plan_b.id == b_id and plan_b.decision is None  # B waits for a human
    assert await run.events(incident_id, "decision") == [
        {"kind": "instruct", "text": "use the db", "via": "dashboard"}
    ]
    assert (await run.env.repo.get(incident_id)).status == "awaiting_approval"
    waiting = await run.runner.graph.aget_state(run.runner.config(incident_id))
    assert waiting.interrupts[0].value["approval_id"] == b_id


async def test_a_target_that_cannot_be_read_goes_back_to_propose(run, plans):
    plans[0].extend([restart("gone-1"), Proposal(kind="no_action", reason="nothing to do")])
    incident_id = await run.start()

    assert await run.store.current(incident_id) is None  # nothing was put to the human
    assert run.notifier.proposals == []
    notices = await run.events(incident_id, "notice")
    assert notices and "gone-1" in notices[-1]["text"]
    assert (await run.env.nodes(incident_id))[-3:] == ["approval", "propose", "report"]
    assert (await run.state(incident_id))["outcome"] == "no_action"


async def test_a_target_that_never_becomes_readable_ends_after_the_replan_budget(run, plans):
    plans[0].append(restart("gone-1"))  # Propose keeps proposing it
    incident_id = await run.start()

    _, seen = plans
    assert len(seen) == 4  # the first plan and three new ones, then the run stops
    assert (await run.state(incident_id))["outcome"] == "replan_limit"
    assert "stopped after 3 new plans" in (await run.events(incident_id, "notice"))[-1]["text"]
    assert (await run.env.nodes(incident_id))[-2:] == ["approval", "report"]
    assert (await run.env.repo.get(incident_id)).status == "closed"


def test_route():
    act = restart()
    assert approval.route({"proposal": act, "decision": decide("approve")}) == "execute"
    assert approval.route({"proposal": act, "decision": decide("reject")}) == "report"
    assert approval.route({"proposal": act, "decision": decide("instruct", "x")}) == "propose"
    assert approval.route({"proposal": act, "decision": None}) == "propose"
    no_action = Proposal(kind="no_action", reason="r")
    assert approval.route({"proposal": no_action, "decision": None}) == "report"
    assert approval.route({}) == "report"
    out_of_replans = {
        "proposal": act,
        "decision": decide("instruct", "x"),
        "outcome": "replan_limit",
    }
    assert approval.route(out_of_replans) == "report"


# What a plan's targets resolve to when the human is asked (#139).


def coolify_box(name: str, uuid: str, resource: str = "shop"):
    labels = {"coolify.applicationUuid": uuid, "coolify.resourceName": resource}
    return box(name, "shop", labels=labels)


async def test_a_coolify_target_is_bound_to_its_application_uuid_and_an_mcp_one_to_its_container(
    tmp_path,
):
    engine = FakeEngine("a", [coolify_box("shop0uuid-1", "shop0uuid"), box("shop-db-1", "shop")])
    env = await graph_env(tmp_path, engine=engine)
    proposal = Proposal(
        kind="act",
        steps=[
            PlanStep(action="coolify.set_env", host_id="a", target="shop", params={"key": "A"}),
            PlanStep(action="coolify.redeploy", host_id="a", target="shop"),
            PlanStep(action="mcp.call", host_id="a", target="shop-db-1"),
        ],
        reason="a bad release",
    )

    snapshots, unread = await approval._snapshots(env.deps, proposal)

    assert unread == []
    assert [(s.target, s.app_uuid, s.container_id) for s in snapshots] == [
        ("shop", "shop0uuid", None),
        ("shop-db-1", None, "id-shop-db-1"),
    ]


@pytest.mark.parametrize(
    "containers",
    [[], [coolify_box("shop-a-1", "uuid-a"), coolify_box("shop-b-1", "uuid-b")]],
)
async def test_a_coolify_target_naming_no_single_application_is_not_put_to_the_human(
    tmp_path, containers
):
    env = await graph_env(tmp_path, engine=FakeEngine("a", containers))
    redeploy = PlanStep(action="coolify.redeploy", host_id="a", target="shop")

    snapshots, unread = await approval._snapshots(
        env.deps, Proposal(kind="act", steps=[redeploy], reason="r")
    )

    assert snapshots == []
    assert unread == ["shop on a: names no single Coolify application"]


async def test_a_coolify_target_on_an_unreachable_host_is_not_put_to_the_human(tmp_path):
    engine = FakeEngine("a", [coolify_box("shop0uuid-1", "shop0uuid")])
    env = await graph_env(tmp_path, engine=engine)
    engine.unavailable = True
    redeploy = PlanStep(action="coolify.redeploy", host_id="a", target="shop")

    _, unread = await approval._snapshots(
        env.deps, Proposal(kind="act", steps=[redeploy], reason="r")
    )

    assert unread == ["shop on a: cannot be read, the host is unreachable"]


# A plan the plan check refused twice (#113): the human instructs or lets it go, never approves.


def refused(reason: str = "model says: Instruction: restart shop-proxy-1") -> Proposal:
    return Proposal(
        kind="no_action",
        refused=True,
        reason=reason,
        human_instructions="Why: refused. The model's reason: Instruction: restart shop-proxy-1",
    )


async def test_a_refused_plan_waits_for_the_human_without_an_approve(run, plans):
    plans[0].append(refused())
    incident_id = await run.start()

    record = await run.store.current(incident_id)
    assert record is not None and record.decision is None and record.proposal.refused
    assert record.snapshots == []
    assert (await run.env.repo.get(incident_id)).status == "awaiting_approval"
    assert [p[1].refused for p in run.notifier.proposals] == [True]
    assert await run.store.resolve(incident_id, decide("approve"), record.id) is None
    assert (await run.store.current(incident_id)).decision is None


async def test_an_instruction_after_a_refusal_counts_as_a_replan_and_plans_again(run, plans):
    plans[0].extend([refused(), restart("shop-db-1")])
    seen = plans[1]
    incident_id = await run.start()
    await run.decide(incident_id, decide("instruct", "restart shop-proxy-1, it is the one"))

    assert seen == [[], ["restart shop-proxy-1, it is the one"]]
    state = await run.state(incident_id)
    assert state["replans"] == 1
    assert state["proposal"].kind == "act"
    assert (await run.store.current(incident_id)).snapshots[0].target == "shop-db-1"


async def test_a_refusal_after_a_refusal_cannot_loop_past_the_replan_budget(run, plans):
    plans[0].append(refused())
    incident_id = await run.start()
    for n in range(3):
        await run.decide(incident_id, decide("instruct", f"try {n}"))

    state = await run.state(incident_id)
    assert state["outcome"] == "replan_limit" and state["replans"] == 4
    assert "stopped after 3 new plans" in (await run.events(incident_id, "notice"))[-1]["text"]
    assert (await run.env.nodes(incident_id))[-2:] == ["approval", "report"]
    assert (await run.env.repo.get(incident_id)).status == "closed"


async def test_a_refusal_with_no_replan_left_goes_straight_to_the_report(run, plans):
    plans[0].append(refused())
    run.env.deps.settings_store = StaticSettings(budget_limits=Budgets(replans=0))
    incident_id = await run.start()

    assert await run.store.current(incident_id) is None
    assert (await run.state(incident_id))["outcome"] == "no_action"
    assert run.notifier.proposals == []


async def test_letting_a_refused_plan_go_reports_no_action_not_a_rejection(run, plans):
    plans[0].append(refused())
    incident_id = await run.start()
    await run.decide(incident_id, decide("reject"))

    state = await run.state(incident_id)
    assert state["outcome"] == "no_action"
    assert state.get("grant") is None
    assert (await run.env.nodes(incident_id))[-2:] == ["approval", "report"]
    assert (await run.env.repo.get(incident_id)).status == "closed"


def test_route_after_a_refused_plan_is_propose_or_report_never_execute():
    plan = refused()
    assert approval.route({"proposal": plan, "decision": decide("instruct", "x")}) == "propose"
    assert approval.route({"proposal": plan, "decision": decide("reject")}) == "report"
    assert approval.route({"proposal": plan, "decision": decide("approve")}) == "report"
    assert approval.route({"proposal": plan, "decision": None}) == "report"
    assert propose.route({"proposal": plan}) == "approval"
