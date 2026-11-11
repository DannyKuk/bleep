import json
from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from bleep import prompts
from bleep.config import Settings
from bleep.graph.replan import PLANNING_AGAIN
from bleep.graph.shown import shown_payload
from bleep.guard.plan_check import check_shown
from bleep.guard.redact import redacted_step
from bleep.hosts.registry import Registry
from bleep.incidents.expectations import DECISION_TEXT
from bleep.incidents.repository import IncidentRepo
from bleep.main import create_app
from bleep.memory.embeddings import FakeEmbedder
from bleep.schemas.graph import (
    Decision,
    Diagnosis,
    PlanStep,
    Proposal,
    Report,
    TargetSnapshot,
    TimelineEntry,
)
from bleep.schemas.incidents import Incident, RecoveryEvent, Signal, Subject
from bleep.schemas.shown import Shown
from bleep.wiring import build_parts
from fakes import FakeEngine, socket_host

T0 = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
REPORT = Report(
    title="container died on a/shop",
    summary="Restarted, not verified.",
    cause="It ran out of memory.",
    action_taken="docker.restart shop-web-1 on a: done",
    outcome="Action taken, not verified",
    timeline=[TimelineEntry(at=T0, text="Approved on the dashboard")],
    follow_ups=["Raise its memory limit."],
    human_decision="Approved on the dashboard",
)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def restart() -> Proposal:
    return Proposal(
        kind="act",
        steps=[PlanStep(action="docker.restart", host_id="a", target="shop-web-1")],
        reason="it exited",
    )


def snap() -> TargetSnapshot:
    return TargetSnapshot(host_id="a", target="shop-web-1", container_id="c1", started_at=T0)


def decided(kind: str, via: str, text: str | None = None) -> Decision:
    return Decision(kind=kind, text=text, via=via, at=T0)


class Resumes:
    """Stands in for `IncidentRunner.resume` and records what it was given."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Decision]] = []
        self.approval_ids: list[str] = []

    async def __call__(self, incident_id: str, decision: Decision, approval_id: str) -> None:
        self.calls.append((incident_id, decision))
        self.approval_ids.append(approval_id)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def app_client(settings, parts, clock):
    """A logged-in client whose incidents open at the test clock's time."""
    parts.incidents = IncidentRepo(parts.db, clock)
    with TestClient(create_app(settings, parts)) as client:
        assert client.post("/api/auth/login", json={"password": "test-password"}).status_code == 204
        yield client


@pytest.fixture
def resumes(app_client, monkeypatch) -> Resumes:
    recorder = Resumes()
    monkeypatch.setattr(app_client.app.state.runner, "resume", recorder)
    return recorder


def call(client: TestClient, fn, *args):
    """Run one of the app's async parts on the app's own event loop."""
    return client.portal.call(fn, *args)


def open_incident(client: TestClient, parts, service: str = "a/shop") -> Incident:
    subject = Subject(kind="service", host_id="a", service_id=service)
    signal = Signal(subject=subject, kind="container_died", at=T0, container="shop-web-1")
    incident, _ = call(client, parts.incidents.open_or_attach, subject, [signal])
    return incident


def ask(client: TestClient, parts, incident_id: str) -> str:
    """Store a pending approval for a restart plan; returns its id."""
    return call(client, parts.approvals.request, incident_id, restart(), [snap()]).id


# --- list ---


def test_list_is_newest_first_with_status_filter_limit_and_offset(app_client, parts, clock):
    first = open_incident(app_client, parts, "a/one")
    clock.now = T0 + timedelta(minutes=1)
    second = open_incident(app_client, parts, "a/two")
    clock.now = T0 + timedelta(minutes=2)
    third = open_incident(app_client, parts, "a/three")
    call(app_client, parts.incidents.set_status, second.id, "closed")

    ids = [i["id"] for i in app_client.get("/api/incidents").json()]
    assert ids == [third.id, second.id, first.id]
    closed = app_client.get("/api/incidents", params={"status": "closed"}).json()
    assert [i["id"] for i in closed] == [second.id]
    page = app_client.get("/api/incidents", params={"limit": 1, "offset": 1}).json()
    assert [i["id"] for i in page] == [second.id]


def open_host_incident(client: TestClient, parts, host_id: str) -> Incident:
    subject = Subject(kind="host", host_id=host_id)
    signal = Signal(subject=subject, kind="host_unreachable", at=T0, detail={"ok": False})
    incident, _ = call(client, parts.incidents.open_or_attach, subject, [signal])
    return incident


def test_list_keeps_a_hosts_own_incidents_with_host_id_not_its_services(app_client, parts, clock):
    first = open_host_incident(app_client, parts, "a")
    call(app_client, parts.incidents.set_status, first.id, "closed")
    clock.now = T0 + timedelta(minutes=1)
    second = open_host_incident(app_client, parts, "a")
    open_incident(app_client, parts, "a/shop")  # a service on the host: not the host's own
    open_host_incident(app_client, parts, "b")  # another host

    own = app_client.get("/api/incidents", params={"host_id": "a"}).json()
    assert [i["id"] for i in own] == [second.id, first.id]
    newest = app_client.get("/api/incidents", params={"host_id": "a", "limit": 1}).json()
    assert [(i["id"], i["kind"]) for i in newest] == [(second.id, "host_unreachable")]
    assert app_client.get("/api/incidents", params={"host_id": "c"}).json() == []


def test_list_items_carry_the_humans_votes_without_the_note(app_client, parts):
    rated = open_incident(app_client, parts, "a/one")
    half = open_incident(app_client, parts, "a/two")
    call(app_client, parts.memory.set_feedback, rated.id, "diagnosis", "up", "spot on hunter2")
    call(app_client, parts.memory.set_feedback, rated.id, "proposal", "up")
    call(app_client, parts.memory.set_feedback, rated.id, "proposal", "down")  # replaces the up
    call(app_client, parts.memory.set_feedback, half.id, "proposal", "up")

    response = app_client.get("/api/incidents")
    votes = {i["id"]: i["votes"] for i in response.json()}
    assert votes[rated.id] == {"diagnosis": "up", "proposal": "down"}
    assert votes[half.id] == {"diagnosis": None, "proposal": "up"}
    assert "hunter2" not in response.text


def test_list_items_carry_their_kind_as_the_fold_key_holds_it(app_client, parts):
    open_incident(app_client, parts, "a/one|two")  # a label may hold the key's separator
    [item] = app_client.get("/api/incidents").json()
    assert item["kind"] == "death"


def test_an_incident_with_no_feedback_has_no_votes(app_client, parts):
    open_incident(app_client, parts)
    [item] = app_client.get("/api/incidents").json()
    assert item["votes"] == {"diagnosis": None, "proposal": None}


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"status": "sleeping"}]
)
def test_list_refuses_out_of_range_paging_and_unknown_statuses(app_client, params):
    assert app_client.get("/api/incidents", params=params).status_code == 422


# --- detail ---


def test_detail_of_an_unknown_incident_is_404(app_client):
    assert app_client.get("/api/incidents/inc_nope").status_code == 404


def test_detail_of_a_fresh_incident_has_nothing_yet(app_client, parts):
    incident = open_incident(app_client, parts)
    body = app_client.get(f"/api/incidents/{incident.id}").json()
    assert body["incident"]["id"] == incident.id
    assert body["incident"]["status"] == "open"
    assert body["diagnosis"] is None
    assert body["proposal"] is None
    assert body["approval"] is None
    assert body["outcome"] is None
    assert body["report"] is None


def test_detail_and_list_carry_the_trace_link(app_client, parts):
    incident = open_incident(app_client, parts)
    assert app_client.get(f"/api/incidents/{incident.id}").json()["incident"]["trace_url"] is None

    url = "http://localhost:3001/project/bleep/traces/0123456789abcdef0123456789abcdef"
    call(app_client, parts.incidents.set_trace_url, incident.id, url)

    detail = app_client.get(f"/api/incidents/{incident.id}").json()
    assert detail["incident"]["trace_url"] == url
    assert app_client.get("/api/incidents").json()[0]["trace_url"] == url


def test_detail_carries_diagnosis_proposal_pending_approval_and_report(
    app_client, parts, monkeypatch
):
    incident = open_incident(app_client, parts)
    approval_id = ask(app_client, parts, incident.id)
    diagnosis = Diagnosis(root_cause="it ran out of memory", category="resource", confidence=0.8)
    state = {
        "diagnosis": diagnosis,
        "proposal": restart(),
        "outcome": "unverified",
        "report": REPORT,
    }

    async def fake_state(incident_id: str) -> dict:
        assert incident_id == incident.id
        return state

    monkeypatch.setattr(app_client.app.state.runner, "state", fake_state)
    body = app_client.get(f"/api/incidents/{incident.id}").json()
    assert body["diagnosis"]["root_cause"] == "it ran out of memory"
    assert body["proposal"]["steps"][0]["target"] == "shop-web-1"
    assert body["outcome"] == "unverified"
    assert body["report"] == REPORT.model_dump(mode="json")
    approval = body["approval"]
    assert approval["id"] == approval_id
    assert approval["status"] == "pending"
    assert approval["decision"] is None
    assert approval["proposal"]["reason"] == "it exited"
    assert "snapshots" not in approval


def test_detail_carries_the_healthy_outcome_of_a_look(app_client, parts, monkeypatch):
    incident = open_incident(app_client, parts)

    async def fake_state(incident_id: str) -> dict:
        return {"proposal": Proposal(kind="no_action", reason="fine"), "outcome": "healthy"}

    monkeypatch.setattr(app_client.app.state.runner, "state", fake_state)
    assert app_client.get(f"/api/incidents/{incident.id}").json()["outcome"] == "healthy"


def test_detail_shows_a_set_env_value_redacted(app_client, parts, monkeypatch):
    value = "postgres://shop:pw-fake-123@attacker.example/shop"
    set_env = Proposal(
        kind="act",
        steps=[
            PlanStep(
                action="coolify.set_env",
                host_id="a",
                target="shop",
                params={"key": "DATABASE_URL", "value": value},
            )
        ],
        reason="the release reads a new variable",
    )
    incident = open_incident(app_client, parts)
    call(app_client, parts.approvals.request, incident.id, set_env, [])

    async def fake_state(incident_id: str) -> dict:
        return {"proposal": set_env}

    monkeypatch.setattr(app_client.app.state.runner, "state", fake_state)
    response = app_client.get(f"/api/incidents/{incident.id}")

    assert "pw-fake-123" not in response.text
    body = response.json()
    shown = "postgres://shop:<redacted>@attacker.example/shop"
    for proposal in (body["proposal"], body["approval"]["proposal"]):
        assert proposal["steps"][0]["params"] == {"key": "DATABASE_URL", "value": shown}
    assert body["proposal"] == body["approval"]["proposal"]  # the dashboard pairs them by value


def test_detail_shows_who_decided_the_latest_approval(app_client, parts):
    incident = open_incident(app_client, parts)
    ask(app_client, parts, incident.id)
    call(app_client, parts.approvals.resolve, incident.id, decided("reject", "telegram"))
    approval = app_client.get(f"/api/incidents/{incident.id}").json()["approval"]
    assert approval["status"] == "rejected"
    assert approval["decision"]["via"] == "telegram"


def test_an_approval_its_incident_was_closed_under_is_reported_closed_not_pending(
    app_client, parts
):
    """Leave alone or a removed host ends the incident through `IncidentRunner.end` (#277)."""
    incident = open_incident(app_client, parts)
    approval_id = ask(app_client, parts, incident.id)
    call(app_client, parts.incidents.set_status, incident.id, "awaiting_approval")
    runner = app_client.app.state.runner
    end = partial(runner.end, ending="quiet")
    assert call(app_client, end, incident.id, Shown.of("notice/left_alone", name="shop"))

    body = app_client.get(f"/api/incidents/{incident.id}").json()
    assert body["incident"]["status"] == "closed"
    assert body["approval"]["id"] == approval_id
    assert body["approval"]["status"] == "closed"
    assert body["approval"]["decision"] is None


def test_a_question_stored_just_after_the_close_is_reported_closed(app_client, parts):
    incident = open_incident(app_client, parts)
    call(app_client, parts.incidents.close_unfinished, incident.id)
    ask(app_client, parts, incident.id)  # the run's Approval node commits after the close

    approval = app_client.get(f"/api/incidents/{incident.id}").json()["approval"]
    assert approval["status"] == "closed"


# --- events ---


def test_events_are_in_seq_order_after_the_cursor(app_client, parts):
    incident = open_incident(app_client, parts)
    for n in range(1, 5):
        call(app_client, parts.bus.publish, incident.id, "notice", {"text": f"n{n}"})
    events = app_client.get(f"/api/incidents/{incident.id}/events", params={"after": 2}).json()
    assert [(e["seq"], e["payload"]["text"]) for e in events] == [(3, "n3"), (4, "n4")]
    assert len(app_client.get(f"/api/incidents/{incident.id}/events").json()) == 4


def test_events_of_an_unknown_incident_are_404(app_client):
    assert app_client.get("/api/incidents/inc_nope/events").status_code == 404


# --- decision ---


def post_decision(client, incident_id, approval_id, kind="approve", text=None):
    body = {"approval_id": approval_id, "kind": kind}
    if text is not None:
        body["text"] = text
    return client.post(f"/api/incidents/{incident_id}/decision", json=body)


def test_a_dashboard_decision_wins_the_approval_and_resumes_the_run(app_client, parts, resumes):
    incident = open_incident(app_client, parts)
    approval_id = ask(app_client, parts, incident.id)

    response = post_decision(app_client, incident.id, approval_id, "approve", "ignored")
    assert response.status_code == 200
    assert response.json()["kind"] == "approve"
    assert response.json()["via"] == "dashboard"
    assert response.json()["text"] is None  # only an instruction carries text

    record = call(app_client, parts.approvals.current, incident.id)
    assert record.decision.via == "dashboard"
    assert [(i, d.kind) for i, d in resumes.calls] == [(incident.id, "approve")]
    assert resumes.approval_ids == [approval_id]  # bound to the plan the dashboard showed


def ask_refused(client: TestClient, parts, incident_id: str) -> str:
    plan = Proposal(kind="no_action", refused=True, reason="refused")
    return call(client, parts.approvals.request, incident_id, plan, []).id


def test_an_approve_on_a_refused_plan_is_409_and_nothing_resumes(app_client, parts, resumes):
    incident = open_incident(app_client, parts)
    approval_id = ask_refused(app_client, parts, incident.id)

    response = post_decision(app_client, incident.id, approval_id, "approve")

    assert response.status_code == 409
    assert "instead" in response.json()["detail"]
    assert resumes.calls == []
    assert call(app_client, parts.approvals.current, incident.id).decision is None


def test_an_approve_on_a_no_action_plan_bleep_could_run_is_409_and_nothing_resumes(
    app_client, parts, resumes
):
    """#365: the plan waits for an instruction or a let-go; nothing runs from it."""
    incident = open_incident(app_client, parts)
    plan = Proposal(
        kind="no_action", reason="x", human_instructions="y", could_run=["docker.restart"]
    )
    plan = plan.model_copy(update={"fix_runnable": True})
    approval_id = call(app_client, parts.approvals.request, incident.id, plan, []).id

    response = post_decision(app_client, incident.id, approval_id, "approve")

    assert response.status_code == 409
    assert "nothing to approve" in response.json()["detail"]
    assert resumes.calls == []
    assert call(app_client, parts.approvals.current, incident.id).decision is None


def test_an_approve_on_a_plan_refused_for_a_hidden_param_is_409(app_client, parts, resumes):
    """#294: the dashboard would have shown `"auth": "<redacted>"` while the real text ran."""
    hidden = PlanStep(
        action="mcp.call",
        host_id="a",
        target="shop-web-1",
        params={"tool": "run_command", "arguments": '{"auth": "x; rm -rf /srv/shop/data"}'},
    )
    [violation] = check_shown(1, hidden)
    plan = Proposal(
        kind="no_action",
        refused=True,
        reason="refused",
        refused_steps=[redacted_step(hidden)],
        refusal_reasons=[violation.text],
    )
    incident = open_incident(app_client, parts)
    approval_id = call(app_client, parts.approvals.request, incident.id, plan, []).id

    response = post_decision(app_client, incident.id, approval_id, "approve")

    assert violation.rule == "hidden_param"
    assert response.status_code == 409
    assert resumes.calls == []
    assert call(app_client, parts.approvals.current, incident.id).decision is None


def test_a_refused_plan_can_be_instructed_or_let_go(app_client, parts, resumes):
    incident = open_incident(app_client, parts)
    approval_id = ask_refused(app_client, parts, incident.id)
    response = post_decision(app_client, incident.id, approval_id, "instruct", "restart it")
    assert response.status_code == 200
    assert resumes.calls[0][1].text == "restart it"

    other = open_incident(app_client, parts, "a/blog")
    other_id = ask_refused(app_client, parts, other.id)
    assert post_decision(app_client, other.id, other_id, "reject").status_code == 200


def test_an_instruction_keeps_its_text_trimmed(app_client, parts, resumes):
    incident = open_incident(app_client, parts)
    approval_id = ask(app_client, parts, incident.id)
    response = post_decision(app_client, incident.id, approval_id, "instruct", "  stop it  ")
    assert response.status_code == 200
    assert resumes.calls[0][1].text == "stop it"


@pytest.mark.parametrize("text", [None, "", "   "])
def test_an_instruction_without_text_is_422(app_client, parts, resumes, text):
    incident = open_incident(app_client, parts)
    approval_id = ask(app_client, parts, incident.id)
    assert post_decision(app_client, incident.id, approval_id, "instruct", text).status_code == 422
    assert resumes.calls == []


def test_a_decision_on_an_already_decided_approval_is_409_naming_the_winner(
    app_client, parts, resumes
):
    incident = open_incident(app_client, parts)
    approval_id = ask(app_client, parts, incident.id)
    call(app_client, parts.approvals.resolve, incident.id, decided("approve", "telegram"))

    response = post_decision(app_client, incident.id, approval_id, "reject")
    assert response.status_code == 409
    assert response.json() == {"detail": "Already decided on Telegram", "decided_via": "telegram"}
    assert resumes.calls == []
    record = call(app_client, parts.approvals.current, incident.id)
    assert record.decision.kind == "approve"  # the first answer stands


def test_a_second_dashboard_decision_names_the_dashboard(app_client, parts, resumes):
    incident = open_incident(app_client, parts)
    approval_id = ask(app_client, parts, incident.id)
    assert post_decision(app_client, incident.id, approval_id, "approve").status_code == 200
    response = post_decision(app_client, incident.id, approval_id, "approve")
    assert response.status_code == 409
    assert response.json() == {
        "detail": "Already decided on the dashboard",
        "decided_via": "dashboard",
    }
    assert len(resumes.calls) == 1


def test_losing_the_race_inside_resolve_is_409_naming_the_winner(
    app_client, parts, resumes, monkeypatch
):
    """Telegram wins between the route's read and its own resolve."""
    incident = open_incident(app_client, parts)
    approval_id = ask(app_client, parts, incident.id)
    resolve = parts.approvals.resolve

    async def telegram_first(incident_id, decision, approval_id=None):
        await resolve(incident_id, decided("reject", "telegram"), approval_id)
        return await resolve(incident_id, decision, approval_id)

    monkeypatch.setattr(parts.approvals, "resolve", telegram_first)
    response = post_decision(app_client, incident.id, approval_id, "approve")
    assert response.status_code == 409
    assert response.json()["decided_via"] == "telegram"
    assert resumes.calls == []


def test_a_yes_that_loses_to_an_outside_close_is_409_saying_it_closed_unanswered(
    app_client, parts, resumes, monkeypatch
):
    """Leave alone closes the incident between the route's read and its own resolve (#277)."""
    incident = open_incident(app_client, parts)
    approval_id = ask(app_client, parts, incident.id)
    resolve = parts.approvals.resolve

    async def closed_first(incident_id, decision, approval_id=None):
        await parts.incidents.close_unfinished(incident_id)
        return await resolve(incident_id, decision, approval_id)

    monkeypatch.setattr(parts.approvals, "resolve", closed_first)
    response = post_decision(app_client, incident.id, approval_id, "approve")
    assert response.status_code == 409
    assert response.json() == {
        "detail": "The incident was closed before anyone answered",
        "decided_via": None,
    }
    assert resumes.calls == []


def test_a_decision_for_a_replaced_plan_is_409_and_decides_nothing(app_client, parts, resumes):
    incident = open_incident(app_client, parts)
    old = ask(app_client, parts, incident.id)
    call(app_client, parts.approvals.resolve, incident.id, decided("instruct", "telegram", "no"))
    ask(app_client, parts, incident.id)  # the new plan after the instruction

    response = post_decision(app_client, incident.id, old, "approve")
    assert response.status_code == 409
    assert response.json() == {
        "detail": "That plan was replaced by a newer one; reload to see it",
        "decided_via": None,
    }
    assert call(app_client, parts.approvals.current, incident.id).decision is None
    assert resumes.calls == []


def test_a_decision_with_nothing_asked_is_409(app_client, parts, resumes):
    incident = open_incident(app_client, parts)
    response = post_decision(app_client, incident.id, "a1", "approve")
    assert response.status_code == 409
    assert response.json() == {"detail": "Nothing is waiting for a decision", "decided_via": None}


def test_a_decision_on_a_finished_incident_is_409(app_client, parts, resumes):
    incident = open_incident(app_client, parts)
    approval_id = ask(app_client, parts, incident.id)
    call(app_client, parts.incidents.set_status, incident.id, "closed")
    response = post_decision(app_client, incident.id, approval_id, "approve")
    assert response.status_code == 409
    assert response.json() == {"detail": "The incident is closed", "decided_via": None}
    assert call(app_client, parts.approvals.current, incident.id).decision is None


def test_a_decision_on_an_unknown_incident_is_404(app_client, resumes):
    assert post_decision(app_client, "inc_nope", "a1").status_code == 404


def test_every_decision_conflict_is_said_in_the_picked_language_with_the_winner_kept(
    app_client, parts, resumes, monkeypatch
):
    monkeypatch.setattr(parts.settings_store, "language", lambda: "de")
    incident = open_incident(app_client, parts)
    nothing = post_decision(app_client, incident.id, "a1", "approve")
    assert nothing.json() == {
        "detail": "Es wartet nichts auf eine Entscheidung",
        "decided_via": None,
    }

    approval_id = ask(app_client, parts, incident.id)
    call(app_client, parts.approvals.resolve, incident.id, decided("approve", "telegram"))
    telegram = post_decision(app_client, incident.id, approval_id, "reject")
    assert telegram.json() == {
        "detail": "Bereits auf Telegram entschieden",
        "decided_via": "telegram",
    }

    other = open_incident(app_client, parts, service="a/other")
    second = ask(app_client, parts, other.id)
    assert post_decision(app_client, other.id, second, "approve").status_code == 200
    again = post_decision(app_client, other.id, second, "approve")
    assert again.json() == {
        "detail": "Bereits im Dashboard entschieden",
        "decided_via": "dashboard",
    }

    closed = open_incident(app_client, parts, service="a/third")
    third = ask(app_client, parts, closed.id)
    call(app_client, parts.incidents.set_status, closed.id, "closed")
    over = post_decision(app_client, closed.id, third, "approve")
    assert over.json() == {"detail": "Der Vorfall ist geschlossen", "decided_via": None}
    assert resumes.calls == [(other.id, resumes.calls[0][1])]


def test_a_rating_with_nothing_to_rate_and_an_unknown_incident_are_said_in_german(
    app_client, parts, monkeypatch
):
    monkeypatch.setattr(parts.settings_store, "language", lambda: "de")
    incident = open_incident(app_client, parts)
    body = {"target": "diagnosis", "vote": "up"}
    rated = app_client.post(f"/api/incidents/{incident.id}/feedback", json=body)
    assert (rated.status_code, rated.json()["detail"]) == (
        409,
        "Es gibt noch keine Diagnose zum Bewerten",
    )
    plan = app_client.post(
        f"/api/incidents/{incident.id}/feedback", json=body | {"target": "proposal"}
    )
    assert plan.json()["detail"] == "Es gibt noch keinen Plan zum Bewerten"
    assert app_client.get("/api/incidents/inc_nope").json() == {
        "detail": "Diesen Vorfall gibt es nicht"
    }


# --- recoveries ---


def test_recoveries_are_newest_first_up_to_the_limit(app_client, parts):
    for minute in range(3):
        event = RecoveryEvent(
            host_id="a",
            container=f"c{minute}",
            at=T0 + timedelta(minutes=minute),
            explanation="came back",
        )
        call(app_client, parts.incidents.log_recovery, event)
    body = app_client.get("/api/recoveries", params={"limit": 2}).json()
    assert [r["container"] for r in body] == ["c2", "c1"]
    assert app_client.get("/api/recoveries", params={"limit": 0}).status_code == 422


# --- "This is expected" ---


@pytest.fixture
def host_client(settings):
    """A client over one socket host, so an expectation can be recorded for it."""

    async def connect(_host):
        return None, FakeEngine("a", [])

    parts = build_parts(settings, Registry([socket_host(id="a")], connect), FakeEmbedder())
    parts.notify.start = lambda runner: None  # type: ignore[method-assign]  # never polls
    with TestClient(create_app(settings, parts)) as client:
        assert client.post("/api/auth/login", json={"password": "test-password"}).status_code == 204
        client.parts = parts  # type: ignore[attr-defined]
        yield client


def unreachable(client) -> Incident:
    subject = Subject(kind="host", host_id="a")
    signal = Signal(subject=subject, kind="host_unreachable", at=T0, detail={"ok": False})
    incident, _ = call(client, client.parts.incidents.open_or_attach, subject, [signal])
    return incident


def test_detail_says_what_this_is_expected_would_record_and_a_post_records_it(host_client):
    incident = unreachable(host_client)
    detail = host_client.get(f"/api/incidents/{incident.id}").json()
    assert detail["expectation"].startswith("A may sleep")

    response = host_client.post(f"/api/incidents/{incident.id}/expected")
    assert response.status_code == 200, response.text
    assert "may sleep" in response.json()["text"]
    after = host_client.get(f"/api/incidents/{incident.id}").json()
    assert after["incident"]["status"] == "closed" and after["expectation"] is None
    assert host_client.parts.registry.hosts()[0].config.may_sleep is True
    assert host_client.post(f"/api/incidents/{incident.id}/expected").status_code == 409


def test_this_is_expected_is_not_offered_for_a_crash_and_404s_for_no_incident(app_client, parts):
    incident = open_incident(app_client, parts)
    assert app_client.get(f"/api/incidents/{incident.id}").json()["expectation"] is None
    assert app_client.post(f"/api/incidents/{incident.id}/expected").status_code == 409
    assert app_client.post("/api/incidents/inc_nope/expected").status_code == 404


def test_the_list_and_the_detail_carry_a_groups_size_and_its_repeats(app_client, parts):
    first = open_incident(app_client, parts)
    call(app_client, parts.incidents.close_unfinished, first.id)
    repeat = open_incident(app_client, parts)
    assert repeat.fold_of == first.id

    listed = {i["id"]: i for i in app_client.get("/api/incidents").json()}
    assert listed[first.id]["repeats"] == listed[repeat.id]["repeats"] == 2
    assert listed[repeat.id]["fold_of"] == first.id
    repeats = app_client.get("/api/incidents", params={"fold_of": first.id}).json()
    assert [i["id"] for i in repeats] == [repeat.id]
    assert app_client.get(f"/api/incidents/{first.id}").json()["repeats"] == 2


def test_the_list_and_the_detail_carry_each_incidents_short_name(app_client, parts):
    first = open_incident(app_client, parts)
    call(app_client, parts.incidents.close_unfinished, first.id)
    second = open_incident(app_client, parts)

    listed = {i["id"]: i for i in app_client.get("/api/incidents").json()}
    assert (listed[first.id]["short_name"], listed[second.id]["short_name"]) == (
        "shop #1",
        "shop #2",
    )
    detail = app_client.get(f"/api/incidents/{second.id}").json()
    assert detail["incident"]["short_name"] == "shop #2"


# --- Bleep's own texts in the picked language (#427) ---


def german(parts, monkeypatch) -> None:
    monkeypatch.setattr(parts.settings_store, "language", lambda: "de")


def test_a_notice_is_shown_in_the_picked_language_and_stays_english_where_it_is_stored(
    app_client, parts, monkeypatch
):
    incident = open_incident(app_client, parts)
    stored = shown_payload(Shown.of("notice/left_alone", name="shop"))
    call(app_client, parts.bus.publish, incident.id, "notice", stored)
    call(app_client, parts.bus.publish, incident.id, "notice", {"text": "from before #427"})
    german(parts, monkeypatch)

    events = app_client.get(f"/api/incidents/{incident.id}/events").json()

    texts = [e["payload"]["text"] for e in events]
    assert texts == ["Geschlossen: Du hast mir gesagt, shop in Ruhe zu lassen.", "from before #427"]
    kept = call(app_client, parts.bus.history, incident.id)
    assert kept[0].payload["text"] == "Closed: you told me to leave shop alone."


def test_in_english_a_notice_reads_as_it_is_stored(app_client, parts):
    incident = open_incident(app_client, parts)
    call(app_client, parts.bus.publish, incident.id, "notice", shown_payload(PLANNING_AGAIN))
    events = app_client.get(f"/api/incidents/{incident.id}/events").json()
    assert events[0]["payload"]["text"] == "Planning again."


def test_the_reports_fixed_texts_and_a_decision_of_bleeps_are_shown_in_the_picked_language(
    app_client, parts, monkeypatch
):
    incident = open_incident(app_client, parts)
    approval_id = ask(app_client, parts, incident.id)
    expected = Decision(
        kind="reject", text="This is expected.", via="telegram", at=T0, shown=DECISION_TEXT
    )
    call(app_client, parts.approvals.resolve, incident.id, expected, approval_id)
    approved = Shown.of("decision/approved", channel=Shown.of("channel/on_dashboard"))
    shown_report = REPORT.model_copy(
        update={
            "timeline": [
                TimelineEntry(at=T0, text="Approved on the dashboard", shown=approved),
                TimelineEntry(at=T0, text="from before #427"),
            ],
            "shown": {
                "outcome": Shown.of("outcome/unverified"),
                "human_decision": approved,
                "cause": Shown.of("report/not_determined"),
            },
        }
    )

    async def fake_state(incident_id: str) -> dict:
        return {"outcome": "unverified", "report": shown_report}

    monkeypatch.setattr(app_client.app.state.runner, "state", fake_state)
    german(parts, monkeypatch)
    body = app_client.get(f"/api/incidents/{incident.id}").json()

    report = body["report"]
    assert report["outcome"] == "Aktion ausgeführt, nicht geprüft"
    assert report["human_decision"] == "Freigegeben im Dashboard"
    assert report["cause"] == "Nicht bestimmt."
    timeline = [entry["text"] for entry in report["timeline"]]
    assert timeline == ["Freigegeben im Dashboard", "from before #427"]
    assert (report["summary"], report["action_taken"]) == (REPORT.summary, REPORT.action_taken)
    assert body["approval"]["decision"]["text"] == "Das ist erwartet."
    assert shown_report.outcome == "Action taken, not verified"  # the stored one is untouched


def test_this_is_expected_says_what_it_records_and_answers_in_the_picked_language(
    host_client, monkeypatch
):
    incident = unreachable(host_client)
    german(host_client.parts, monkeypatch)
    detail = host_client.get(f"/api/incidents/{incident.id}").json()
    assert detail["expectation"].startswith("A darf schlafen: Solange er nicht antwortet")

    response = host_client.post(f"/api/incidents/{incident.id}/expected")
    assert response.json()["text"].startswith("Du hast gesagt, dass das erwartet ist. A darf")
    again = host_client.post(f"/api/incidents/{incident.id}/expected")
    assert again.status_code == 409
    assert again.json()["detail"] == "Hier lässt sich gerade nichts als erwartet markieren."
    events = call(host_client, host_client.parts.bus.history, incident.id)
    kept = [e.payload["text"] for e in events if e.kind in ("expected", "notice")]
    assert kept[0].startswith("You said this is expected. A may sleep")
    assert kept[1] == "Closed: you said this is expected."


# --- what is stored and what a model reads, with the language picked (#456) ---


async def dump_stored(db) -> list[str]:
    """Every row of every table but the sessions, without the times, as sorted JSON lines."""
    conn = await db.connect()
    try:
        async with conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'") as cursor:
            tables = [row["name"] for row in await cursor.fetchall()]
        lines = []
        for table in sorted(set(tables) - {"sessions"}):
            if table.startswith("knowledge"):
                continue  # the background ingest of the docs, which is not a refusal
            if table.startswith("sqlite_"):
                continue
            async with conn.execute(f"SELECT * FROM {table}") as cursor:  # noqa: S608
                for row in await cursor.fetchall():
                    kept = {
                        k: v for k, v in dict(row).items() if k != "at" and not k.endswith("_at")
                    }
                    lines.append(f"{table} {json.dumps(kept, sort_keys=True, default=str)}")
        return sorted(lines)
    finally:
        await conn.close()


def model_prompts() -> dict[str, str]:
    """Every prompt a model reads (not a text a person reads), loaded with placeholders filled."""
    names = [
        p.relative_to(prompts.PROMPTS_DIR).with_suffix("").as_posix()
        for p in prompts.all_prompt_files()
    ]
    return {
        name: prompts.load(
            name, **dict.fromkeys(prompts.placeholders(prompts.file_text(name)), "x")
        )
        for name in names
        if not name.startswith(prompts.TRANSLATED_AREAS) or name in prompts.STAYS_ENGLISH
    }


def refused_flow(tmp_path, clock, language: str) -> tuple[list[str], list[str], dict[str, str]]:
    """The same refusals in an app of its own with `language` picked: a decision refused three
    ways, a feedback with nothing to rate, settings saves that are stale, name a host that is not
    there or fail validation, and dev panel overrides that are refused, saved and reset. Returns
    what the API said, what is stored, and what the models read afterwards."""
    settings = Settings(
        data_dir=tmp_path / language,
        hosts_file=tmp_path / language / "hosts.yaml",
        admin_password=SecretStr("test-password"),
        secret=SecretStr("test-secret"),
        dev_panel=True,
    )
    parts = build_parts(settings, Registry([]), embedder=FakeEmbedder())
    parts.incidents = IncidentRepo(parts.db, clock)
    parts.settings_store.language = lambda: language
    said: list[str] = []
    with TestClient(create_app(settings, parts)) as client:
        assert client.post("/api/auth/login", json={"password": "test-password"}).status_code == 204
        incident = open_incident(client, parts)
        approval_id = ask(client, parts, incident.id)
        said.append(post_decision(client, incident.id, "a-old", "approve").json()["detail"])
        call(client, parts.approvals.resolve, incident.id, decided("approve", "telegram"))
        said.append(post_decision(client, incident.id, approval_id, "reject").json()["detail"])
        call(client, parts.incidents.set_status, incident.id, "closed")
        said.append(post_decision(client, incident.id, approval_id, "reject").json()["detail"])
        body = {"target": "diagnosis", "vote": "up"}
        feedback = client.post(f"/api/incidents/{incident.id}/feedback", json=body)
        said.append(feedback.json()["detail"])

        version = {"If-Match": client.get("/api/settings").json()["version"]}
        for json_body, headers in (
            ({}, {"If-Match": "stale"}),
            ({"hosts": {"nowhere": {"may_sleep": True}}}, version),
            ({"thresholds": {"repeated_deaths": 101}}, version),
            ({"general": {"timezone": "Mars/Base"}}, version),
        ):
            answer = client.put("/api/settings", json=json_body, headers=headers)
            said.append(json.dumps(answer.json()["detail"], ensure_ascii=False))

        name = "llm/json_retry"
        listed = client.get("/api/dev/prompts").json()
        current = next(item["version"] for item in listed if item["name"] == name)
        for text in ("a { b", "{0}", "nothing"):
            answer = client.put(
                f"/api/dev/prompts/{name}", json={"text": text}, headers={"If-Match": current}
            )
            said.append(answer.json()["detail"])
        saved = client.put(
            f"/api/dev/prompts/{name}",
            json={"text": prompts.file_text(name)},
            headers={"If-Match": current},
        )
        client.delete(f"/api/dev/prompts/{name}", headers={"If-Match": saved.json()["version"]})

        call(client, parts.guard_log.flush)  # the guard log writes in the background
        stored = call(client, dump_stored, parts.db)
        events = call(client, parts.bus.history, incident.id)
        stored += [f"event {e.kind} {json.dumps(e.payload, sort_keys=True)}" for e in events]
        stored.append(json.dumps(parts.settings_store.overrides().model_dump(mode="json")))
        stored = [
            line.replace(incident.id, "<incident>").replace(approval_id, "<approval>")
            for line in stored
        ]
        return said, stored, model_prompts()


def test_the_picked_language_changes_nothing_that_is_stored_and_nothing_a_model_reads(
    tmp_path, clock
):
    english_said, english_stored, english_read = refused_flow(tmp_path, clock, "en")
    german_said, german_stored, german_read = refused_flow(tmp_path, clock, "de")

    assert len(english_said) == len(german_said) == 11
    assert all(en != de for en, de in zip(english_said, german_said, strict=True))
    assert german_stored == english_stored
    assert any(line.startswith("guard_log ") for line in english_stored)
    assert any(line.startswith("incidents ") for line in english_stored)
    assert german_read == english_read
    assert "graph/propose" in english_read and "talk/read" in english_read
