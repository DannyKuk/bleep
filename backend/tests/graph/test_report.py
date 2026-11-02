import json
from datetime import datetime, timedelta
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from bleep.graph.deps import StaticSettings
from bleep.graph.nodes import report
from bleep.guard.pipeline import guard_text
from bleep.guard.report_check import MARKER
from bleep.schemas.graph import (
    Decision,
    Diagnosis,
    Evidence,
    GatherContext,
    PlanStep,
    Proposal,
    Report,
    StepResult,
    VerificationPlan,
)
from bleep.schemas.incidents import Signal, Subject
from bleep.schemas.settings import Budgets
from fakes import FakeChatModel
from graph_env import T0, graph_env

LOG_LINE = (
    "2026-11-02T12:00:01Z ERROR shop-web sqlalchemy.exc.OperationalError: could not translate "
    'host name "wrong-db" to address: Name or service not known (Background on this error at: '
    "https://sqlalche.me/e/20/e3q8)"
)
TITLE = "container died on a/compose:shop"  # the repository's title for the incident
DIAGNOSIS = Diagnosis(
    root_cause="shop-web was stopped and its restart policy is no",
    category="config",
    evidence=[Evidence(source="gather", detail="exit code 137, no restart policy")],
    confidence=0.9,
    affected_services=["shop"],
)
STEP = PlanStep(action="docker.start", host_id="a", target="shop-web-1")
RESULT = StepResult(
    step_index=0, action="docker.start", target="shop-web-1", ok=True, detail="started shop-web-1"
)
ACT = Proposal(
    kind="act",
    steps=[STEP],
    reason="Starting it brings the shop back",
    verification=VerificationPlan(containers=["shop-web-1"]),
)
NO_ACTION = Proposal(
    kind="no_action",
    reason="Bleep cannot change an environment variable",
    human_instructions="Fix DATABASE_URL in the Compose file and recreate shop-web.",
)


def decide(kind: str, via: str = "dashboard", text: str | None = None) -> Decision:
    return Decision(kind=kind, via=via, text=text, at=T0 + timedelta(minutes=5))


def written(**fields: Any) -> AIMessage:
    """The model's report, as plain JSON (the fake model has no tool support)."""
    body = {
        "summary": "shop-web was down and Bleep started it again.",
        "cause": "Someone stopped shop-web and nothing restarted it.",
        "follow_ups": ["Set a restart policy on shop-web."],
    }
    return AIMessage(json.dumps(body | fields))


class Ticker:
    """A clock that moves one second each time it is read."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


class Notified:
    def __init__(self, fail: bool = False) -> None:
        self.reports: list[tuple[str, Report]] = []
        self.fail = fail

    async def report(self, incident, report: Report) -> None:
        if self.fail:
            raise RuntimeError("telegram is down")
        self.reports.append((incident.id, report))


async def setup(tmp_path, *script: AIMessage, notifier: Notified | None = None):
    env = await graph_env(tmp_path)
    env.models.model = FakeChatModel(script=list(script), structured_output_unsupported=True)
    env.bus.clock = Ticker(T0 + timedelta(minutes=1))
    notified = notifier or Notified()
    env.deps.notifier = notified  # type: ignore[assignment]
    subject = Subject(kind="service", host_id="a", service_id=env.service_id("shop"))
    incident = await env.open(subject, container="shop-web-1")
    state: dict[str, Any] = {
        "incident_id": incident.id,
        "subject": subject,
        "signals": [Signal(subject=subject, kind="container_died", at=T0, container="shop-web-1")],
        "context": GatherContext(at=T0),
        "diagnosis": DIAGNOSIS,
    }
    return env, state, notified


async def run(env, state) -> dict:
    return await report.make(env.deps)(state)


async def test_an_unverified_run_never_says_fixed(tmp_path):
    claims = written(
        summary="Bleep started shop-web. The shop is fixed now.",
        cause="Fixed: a stopped container.",
        follow_ups=["Nothing, it is fixed.", "Set a restart policy on shop-web."],
    )
    env, state, _ = await setup(tmp_path, claims)
    state |= {
        "proposal": ACT,
        "decision": decide("approve"),
        "step_results": [RESULT],
    }

    update = await run(env, state)

    assert update["outcome"] == "unverified"
    written_report: Report = update["report"]
    assert "fixed" not in written_report.model_dump_json().lower()
    assert written_report.outcome == "Action taken, not verified"
    assert written_report.summary == "Bleep started shop-web."
    assert written_report.follow_ups == ["Set a restart policy on shop-web."]
    assert written_report.action_taken == "docker.start shop-web-1 on a: done"


async def test_only_a_verified_run_is_called_fixed(tmp_path):
    env, state, _ = await setup(tmp_path, written(summary="Started shop-web; it is fixed."))
    state |= {
        "proposal": ACT,
        "decision": decide("approve"),
        "step_results": [RESULT],
        "outcome": "resolved",
    }

    update = await run(env, state)

    assert update["outcome"] == "resolved"
    assert update["report"].outcome == "Fixed and verified"
    assert update["report"].summary == "Started shop-web; it is fixed."
    assert (await env.repo.get(state["incident_id"])).status == "resolved"


async def test_a_model_that_pastes_a_log_line_has_it_removed(tmp_path):
    pasted = written(summary=f"shop-web failed with: {LOG_LINE}", follow_ups=[LOG_LINE])
    env, state, notified = await setup(tmp_path, pasted)
    logs, _ = guard_text("logs.shop-web-1", f"INFO start\n{LOG_LINE}\n")
    state |= {"context": GatherContext(at=T0, logs={"shop-web-1": logs}), "proposal": NO_ACTION}

    update = await run(env, state)

    stored: Report = update["report"]
    assert LOG_LINE not in stored.model_dump_json()
    assert stored.summary == f"shop-web failed with: {MARKER}"
    assert stored.follow_ups == [MARKER]
    sent = notified.reports[0][1]
    assert LOG_LINE not in sent.model_dump_json()
    kinds = [kind for _, kind, _ in env.guard_log.records]
    assert kinds == ["redacted"]


async def test_a_log_line_read_by_a_tool_is_removed_too(tmp_path):
    env, state, _ = await setup(tmp_path, written(cause=LOG_LINE))
    tool_out, _ = guard_text("container_logs", f"{LOG_LINE}\n")
    state |= {
        "proposal": NO_ACTION,
        "messages": [ToolMessage(tool_out, tool_call_id="c1", name="container_logs")],
    }

    update = await run(env, state)

    assert update["report"].cause == MARKER


async def test_the_timeline_is_ordered_and_names_the_decision_and_its_channel(tmp_path):
    env, state, _ = await setup(tmp_path, written())
    incident_id = state["incident_id"]
    for kind, payload in [
        ("node_started", {"node": "gather"}),
        ("node_finished", {"node": "gather"}),
        ("node_started", {"node": "investigate"}),
        ("tool_call", {"name": "inspect_container", "args": {}}),
        ("tool_result", {"name": "inspect_container", "chars": 10, "summary_first_line": "x"}),
        ("tool_call", {"name": "container_logs", "args": {}}),
        ("node_finished", {"node": "investigate"}),
        ("node_finished", {"node": "diagnose"}),
        ("proposal", ACT.model_dump(mode="json")),
        ("approval_requested", {"approval_id": "ap1", "plan_hash": "h"}),
        ("reminder", {"approval_id": "ap1"}),
        ("decision", {"kind": "approve", "text": None, "via": "telegram"}),
        ("action", {"action": "docker.start", "target": "shop-web-1", "status": "done"}),
        ("verification", {"ok": True, "attempt": 1, "checks": []}),
        ("node_started", {"node": "report"}),
    ]:
        await env.bus.publish(incident_id, kind, payload)
    state |= {"proposal": ACT, "decision": decide("approve", via="telegram"), "outcome": "resolved"}

    update = await run(env, state)

    stored: Report = update["report"]
    assert [entry.text for entry in stored.timeline] == [
        "Signal: container died (shop-web-1)",
        "Gathered the context",
        "Investigated with 2 tool calls",
        "Diagnosed",
        "Proposed: docker.start shop-web-1 on a",
        "Asked for approval",
        "Reminder sent",
        "Approved on Telegram",
        "docker.start shop-web-1: done",
        "Check 1 passed",
    ]
    times = [entry.at for entry in stored.timeline]
    assert times == sorted(times) and times[0] == T0
    assert stored.human_decision == "Approved on Telegram"


async def test_an_instruction_and_a_dashboard_rejection_are_named(tmp_path):
    env, state, _ = await setup(tmp_path, written())
    incident_id = state["incident_id"]
    await env.bus.publish(
        incident_id, "decision", {"kind": "instruct", "text": "use the db", "via": "telegram"}
    )
    await env.bus.publish(incident_id, "decision", {"kind": "reject", "via": "dashboard"})
    state |= {"proposal": ACT, "decision": decide("reject")}

    update = await run(env, state)

    texts = [entry.text for entry in update["report"].timeline]
    assert texts[-2:] == ["Instruction from Telegram: use the db", "Rejected on the dashboard"]
    assert update["report"].human_decision == "Rejected on the dashboard"


@pytest.mark.parametrize(
    ("extra", "outcome", "text"),
    [
        ({"proposal": ACT, "decision": decide("reject")}, "rejected", "Rejected, nothing was done"),
        ({"proposal": NO_ACTION}, "no_action", "No action proposed, nothing was done"),
    ],
)
async def test_rejected_and_no_action_get_a_report_without_action(tmp_path, extra, outcome, text):
    env, state, notified = await setup(tmp_path, written())
    state |= extra

    update = await run(env, state)

    assert update["outcome"] == outcome
    stored: Report = update["report"]
    assert stored.action_taken is None
    assert stored.outcome == text
    assert stored.title == TITLE
    assert notified.reports == [(state["incident_id"], stored)]
    assert (await env.repo.get(state["incident_id"])).status == "closed"


async def test_no_action_keeps_the_humans_instructions_as_a_follow_up_without_a_model(tmp_path):
    env, state, _ = await setup(tmp_path, AIMessage("not json at all"), AIMessage("still not"))
    state |= {"proposal": NO_ACTION}

    update = await run(env, state)

    stored: Report = update["report"]
    assert stored.summary == f"{TITLE}: no action proposed, nothing was done."
    assert stored.cause == DIAGNOSIS.root_cause
    assert stored.follow_ups == [NO_ACTION.human_instructions]


OUTCOMES = {
    "resolved": "Fixed and verified",
    "unverified": "Action taken, not verified",
    "rejected": "Rejected, nothing was done",
    "no_action": "No action proposed, nothing was done",
    "unsure": "Cause not found, nothing was done",
    "budget": "Stopped, the model budget is spent",
}


@pytest.mark.parametrize("outcome", list(OUTCOMES))
async def test_every_outcome_is_reported_stored_published_and_closed(tmp_path, outcome):
    env, state, notified = await setup(tmp_path, written())
    state |= {"proposal": ACT, "decision": decide("approve"), "outcome": outcome}

    update = await run(env, state)

    stored: Report = update["report"]
    assert update["outcome"] == outcome
    assert stored.outcome == OUTCOMES[outcome]
    assert notified.reports == [(state["incident_id"], stored)]
    events = [e for e in await env.bus.history(state["incident_id"]) if e.kind == "report"]
    assert [Report.model_validate(e.payload) for e in events] == [stored]
    status = (await env.repo.get(state["incident_id"])).status
    assert status == ("resolved" if outcome == "resolved" else "closed")
    if outcome != "resolved":
        assert "fixed" not in stored.model_dump_json().lower()


async def test_a_spent_budget_writes_the_report_without_the_model(tmp_path):
    env, state, notified = await setup(tmp_path)  # an empty script: any model call fails
    state |= {"proposal": NO_ACTION, "outcome": "budget"}
    env.deps.settings_store = StaticSettings(budget_limits=Budgets(daily_cost_usd=0))

    update = await run(env, state)

    assert env.models.model.cursor == 0
    assert update["report"].outcome == "Stopped, the model budget is spent"
    assert len(notified.reports) == 1


async def test_a_failing_model_or_channel_still_ends_with_a_report(tmp_path):
    env, state, _ = await setup(tmp_path, notifier=Notified(fail=True))  # no replies scripted
    state |= {"proposal": ACT, "decision": decide("reject")}

    update = await run(env, state)

    assert update["report"].summary == f"{TITLE}: rejected, nothing was done."
    assert (await env.repo.get(state["incident_id"])).status == "closed"
    assert [e.kind for e in await env.bus.history(state["incident_id"])] == ["report"]


async def test_the_prompt_carries_no_log_text(tmp_path):
    env, state, _ = await setup(tmp_path, written())
    logs, _ = guard_text("logs.shop-web-1", f"{LOG_LINE}\n")
    state |= {"context": GatherContext(at=T0, logs={"shop-web-1": logs}), "proposal": NO_ACTION}

    await run(env, state)

    sent = "\n".join(m.text for m in env.models.model.seen[0])
    assert "wrong-db" not in sent
    assert DIAGNOSIS.root_cause in sent
    assert "No action proposed, nothing was done" in sent


def test_outcome_rules_hold():
    assert report.outcome({"outcome": "budget", "proposal": ACT}) == "budget"
    assert report.outcome({"proposal": ACT, "decision": decide("reject")}) == "rejected"
    assert report.outcome({"proposal": NO_ACTION}) == "no_action"
    assert report.outcome({}) == "no_action"
    # approved, but nothing checked: never "resolved"
    assert report.outcome({"proposal": ACT, "decision": decide("approve")}) == "unverified"
