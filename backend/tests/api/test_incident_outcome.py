"""The outcome of an incident closed while its plan waited, its service healthy again on its own
(#571), as `GET /api/incidents/{id}` serves it. The other outcomes come from the run's checkpoint
and are tested with the incident API."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from bleep.main import create_app
from bleep.schemas.graph import Proposal
from bleep.schemas.incidents import Signal, Subject
from bleep.schemas.shown import Shown

SHOP = Subject(kind="service", host_id="a", service_id="a/compose:shop")


@pytest.fixture
def client(settings, parts):
    with TestClient(create_app(settings, parts)) as client:
        assert client.post("/api/auth/login", json={"password": "test-password"}).status_code == 204
        yield client


def waiting(client: TestClient, parts) -> str:
    def call(fn, *args):
        return client.portal.call(lambda: fn(*args))

    at = datetime(2026, 11, 13, 17, 51, tzinfo=UTC)
    signal = Signal(subject=SHOP, kind="container_died", at=at, container="shop-web-1")
    incident, _ = call(parts.incidents.open_or_attach, SHOP, [signal])
    call(parts.approvals.request, incident.id, Proposal(kind="no_action", reason="r"), [])
    call(parts.incidents.set_status, incident.id, "awaiting_approval")
    return incident.id


def test_an_incident_closed_as_back_on_its_own_is_served_with_the_outcome_recovered(client, parts):
    incident_id = waiting(client, parts)
    text = Shown.of("notice/recovered", name="shop")
    runner = client.app.state.runner

    assert client.portal.call(lambda: runner.healthy_again(incident_id, text))
    body = client.get(f"/api/incidents/{incident_id}").json()

    assert body["incident"]["status"] == "closed"
    assert body["outcome"] == "recovered"
    assert body["approval"]["status"] == "closed"


def test_an_incident_closed_any_other_way_has_no_outcome_of_its_own(client, parts):
    incident_id = waiting(client, parts)
    client.portal.call(lambda: parts.incidents.close_unfinished(incident_id))

    body = client.get(f"/api/incidents/{incident_id}").json()

    assert body["incident"]["status"] == "closed"
    assert body["outcome"] is None
