from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from bleep.incidents.repository import IncidentRepo
from bleep.main import create_app
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


def test_detail_and_list_carry_the_trace_link_and_no_tracing_key(app_client, parts):
    incident = open_incident(app_client, parts)
    assert app_client.get(f"/api/incidents/{incident.id}").json()["incident"]["trace_url"] is None

    url = "http://localhost:3001/trace/0123456789abcdef0123456789abcdef"
    call(app_client, parts.incidents.set_trace_url, incident.id, url)

    detail = app_client.get(f"/api/incidents/{incident.id}")
    assert detail.json()["incident"]["trace_url"] == url
    assert app_client.get("/api/incidents").json()[0]["trace_url"] == url
    assert "LANGFUSE" not in detail.text.upper() and "secret" not in detail.text.lower()


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


def test_detail_shows_who_decided_the_latest_approval(app_client, parts):
    incident = open_incident(app_client, parts)
    ask(app_client, parts, incident.id)
    call(app_client, parts.approvals.resolve, incident.id, decided("reject", "telegram"))
    approval = app_client.get(f"/api/incidents/{incident.id}").json()["approval"]
    assert approval["status"] == "rejected"
    assert approval["decision"]["via"] == "telegram"


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
