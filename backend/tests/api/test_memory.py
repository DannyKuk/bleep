"""Feedback on an incident's findings, a service's facts, and the service page's read (#30)."""

from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from bleep.graph.nodes import gather
from bleep.hosts.config import ServiceOverrides, UrlCheckConfig
from bleep.hosts.registry import Registry
from bleep.incidents.repository import IncidentRepo
from bleep.main import create_app
from bleep.memory.store import MemoryStore
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.graph import Diagnosis, PlanStep, Proposal, Report
from bleep.schemas.incidents import Incident, Signal, Subject
from fakes import FakeEngine, compose_labels, ssh_host

T0 = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
SHOP = "a/compose:shop"
SHOP_PATH = f"/api/services/{quote(SHOP, safe='')}"
DIAGNOSIS = Diagnosis(root_cause="the db was down", category="dependency", confidence=0.8)
RESTART = Proposal(
    kind="act",
    steps=[PlanStep(action="docker.restart", host_id="a", target="shop-web-1")],
    reason="it exited",
)


def web(name: str = "shop-web-1") -> ContainerSummary:
    return ContainerSummary(
        id=f"id-{name}",
        name=name,
        image="shop:1",
        state="running",
        labels=compose_labels("shop"),
    )


@pytest.fixture
def app_client(settings, parts):
    """A logged-in client over host `a` with the `shop` service, its URL and its rules."""
    host = ssh_host(
        id="a",
        rules=["never restart postgres without asking"],
        services={
            "shop": ServiceOverrides(
                rules=["the shop db is shared with billing"],
                urls=[UrlCheckConfig(url="https://shop.example.test/health")],
            )
        },
    )

    async def connect(_host):
        return None, FakeEngine("a", [web()])

    parts.registry = Registry([host], connect, clock=lambda: T0)
    parts.incidents = IncidentRepo(parts.db, lambda: T0)
    parts.memory = MemoryStore(parts.db, parts.memory.embedder, clock=lambda: T0)
    with TestClient(create_app(settings, parts)) as client:
        assert client.post("/api/auth/login", json={"password": "test-password"}).status_code == 204
        yield client


def call(client: TestClient, fn, *args):
    return client.portal.call(fn, *args)


def open_incident(client: TestClient, parts, service: str = SHOP) -> Incident:
    subject = Subject(kind="service", host_id="a", service_id=service)
    signal = Signal(subject=subject, kind="container_died", at=T0, container="shop-web-1")
    incident, _ = call(client, parts.incidents.open_or_attach, subject, [signal])
    return incident


@pytest.fixture
def findings(app_client, monkeypatch):
    """The run's checkpoint, as the feedback route and the detail read it."""
    state: dict = {"diagnosis": DIAGNOSIS, "proposal": RESTART}

    async def fake_state(_incident_id: str) -> dict:
        return state

    monkeypatch.setattr(app_client.app.state.runner, "state", fake_state)
    return state


def remembered(client: TestClient, parts, incident: Incident) -> None:
    report = Report(
        title="container died on shop",
        summary="s",
        cause="the db was down",
        outcome="Rejected, nothing was done",
    )
    call(client, parts.memory.add_report, incident, report)


# --- feedback ---


def test_a_thumbs_down_on_a_diagnosis_reaches_the_next_similar_incident(
    app_client, parts, findings
):
    incident = open_incident(app_client, parts)
    call(app_client, parts.incidents.set_status, incident.id, "closed")
    remembered(app_client, parts, incident)

    answer = app_client.post(
        f"/api/incidents/{incident.id}/feedback",
        json={"target": "diagnosis", "vote": "down", "note": "  it was the disk  "},
    )

    assert answer.status_code == 200
    assert answer.json() == {
        "target": "diagnosis",
        "vote": "down",
        "note": "it was the disk",
        "at": "2026-11-02T10:00:00Z",
    }
    [past] = call(app_client, parts.memory.similar, "shop: container_died", SHOP)
    assert past.feedback == "the diagnosis was wrong (it was the disk)"


def test_feedback_is_stored_with_the_incident_and_a_second_vote_replaces_the_first(
    app_client, parts, findings
):
    incident = open_incident(app_client, parts)
    path = f"/api/incidents/{incident.id}/feedback"

    assert app_client.post(path, json={"target": "proposal", "vote": "down"}).status_code == 200
    assert app_client.post(path, json={"target": "proposal", "vote": "up"}).status_code == 200
    app_client.post(path, json={"target": "diagnosis", "vote": "up", "note": ""})

    feedback = app_client.get(f"/api/incidents/{incident.id}").json()["feedback"]
    assert [(f["target"], f["vote"], f["note"]) for f in feedback] == [
        ("diagnosis", "up", None),
        ("proposal", "up", None),
    ]


def test_feedback_needs_the_finding_it_rates(app_client, parts, findings):
    incident = open_incident(app_client, parts)
    findings.clear()

    answer = app_client.post(
        f"/api/incidents/{incident.id}/feedback", json={"target": "proposal", "vote": "up"}
    )

    assert answer.status_code == 409
    assert answer.json()["detail"] == "There is no plan to rate yet"


@pytest.mark.parametrize(
    "body",
    [
        {"target": "report", "vote": "up"},
        {"target": "diagnosis", "vote": "sideways"},
        {"target": "diagnosis", "vote": "up", "note": "x" * 201},
    ],
)
def test_feedback_refuses_unknown_targets_votes_and_long_notes(app_client, parts, findings, body):
    incident = open_incident(app_client, parts)
    assert app_client.post(f"/api/incidents/{incident.id}/feedback", json=body).status_code == 422


def test_feedback_on_an_unknown_incident_is_404(app_client, findings):
    body = {"target": "diagnosis", "vote": "up"}
    assert app_client.post("/api/incidents/inc_nope/feedback", json=body).status_code == 404


# --- facts ---


def test_facts_are_added_listed_and_deleted(app_client):
    added = app_client.post(f"{SHOP_PATH}/facts", json={"text": "  the worker is batch only "})

    assert added.status_code == 200
    fact = added.json()
    assert fact == {
        "id": fact["id"],
        "text": "the worker is batch only",
        "source": "human",
        "created_at": "2026-11-02T10:00:00Z",
        "incident_id": None,
    }
    assert app_client.get(f"{SHOP_PATH}/facts").json() == [fact]
    assert app_client.delete(f"{SHOP_PATH}/facts/{fact['id']}").status_code == 204
    assert app_client.get(f"{SHOP_PATH}/facts").json() == []
    assert app_client.delete(f"{SHOP_PATH}/facts/{fact['id']}").status_code == 404


def test_deleting_a_fact_removes_it_from_recall(app_client, parts):
    kept = call(app_client, parts.memory.add_fact, SHOP, "the db is shared with billing")
    learned = call(
        app_client, parts.memory.replace_report_facts, SHOP, "inc-1", ["always redeploy"]
    )
    [fact] = learned

    assert app_client.delete(f"{SHOP_PATH}/facts/{fact.id}").status_code == 204

    # The next incident's Gather, the way the graph reads memory, no longer sees it.
    deps = replace(parts.graph_deps, memory=parts.memory, registry=parts.registry)
    subject = Subject(kind="service", host_id="a", service_id=SHOP)
    state = {"incident_id": "inc-next", "subject": subject, "signals": []}
    context = call(app_client, gather.make(deps), state)["context"]
    assert context.facts == [kept]


def test_a_fact_is_deleted_only_through_its_own_service(app_client, parts):
    fact = call(app_client, parts.memory.add_fact, "a/compose:blog", "the blog is static")

    assert app_client.delete(f"{SHOP_PATH}/facts/{fact.id}").status_code == 404
    assert call(app_client, parts.memory.facts, "a/compose:blog") == [fact]


def test_a_fact_needs_text_and_a_service_bleep_knows(app_client):
    assert app_client.post(f"{SHOP_PATH}/facts", json={"text": "   "}).status_code == 422
    assert app_client.post(f"{SHOP_PATH}/facts", json={"text": "x" * 201}).status_code == 422
    unknown = app_client.post("/api/services/a%2Fcompose%3Anope/facts", json={"text": "hi"})
    assert unknown.status_code == 404


# --- the service page ---


def test_a_service_whose_host_is_gone_is_a_404_not_a_500(app_client, parts, monkeypatch):
    """The host list and the services are read apart; a host gone in between is not found."""
    monkeypatch.setattr(parts.registry, "hosts", lambda: [])

    answer = app_client.get(SHOP_PATH)

    assert answer.status_code == 404


def test_the_service_detail_has_its_urls_and_rules(app_client):
    body = app_client.get(SHOP_PATH).json()

    assert body["service"]["id"] == SHOP
    assert body["service"]["name"] == "shop"
    assert [u["url"] for u in body["service"]["urls"]] == ["https://shop.example.test/health"]
    assert body["rules"] == ["the shop db is shared with billing"]
    assert body["host_rules"] == ["never restart postgres without asking"]


def test_the_service_detail_of_an_unknown_service_is_404(app_client):
    assert app_client.get("/api/services/a%2Fcompose%3Anope").status_code == 404


def test_past_incidents_are_listed_per_service(app_client, parts):
    shop = open_incident(app_client, parts)
    open_incident(app_client, parts, "a/compose:blog")

    listed = app_client.get("/api/incidents", params={"service_id": SHOP}).json()

    assert [i["id"] for i in listed] == [shop.id]
