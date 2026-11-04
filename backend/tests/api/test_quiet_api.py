"""The quiet routes (#220) and quiet on the service and host views."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from bleep.hosts.registry import Registry
from bleep.main import create_app
from bleep.schemas.docker import ContainerSummary
from fakes import FakeEngine, ssh_host

SHOP = "a/compose:shop"
SHOP_PATH = f"/api/quiet/services/{SHOP}"


def box(name: str, state: str = "running", project: str = "shop") -> ContainerSummary:
    return ContainerSummary(
        id=f"id-{name}",
        name=name,
        image="img",
        state=state,
        labels={"com.docker.compose.project": project},
    )


def in_hours(hours: float) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


@pytest.fixture
def client(settings, parts):
    hosts = [ssh_host(id="a", name="Host A")]
    engines = {"a": FakeEngine("a", [box("shop-web-1", "exited"), box("api-1", project="api")])}

    async def connect(host):
        return None, engines[host.id]

    parts.registry = Registry(hosts, connect)
    with TestClient(create_app(settings, parts)) as client:
        client.post("/api/auth/login", json={"password": "test-password"})
        yield client


def shop(client):
    return next(s for s in client.get("/api/services").json() if s["id"] == SHOP)


def host_a(client):
    return next(h for h in client.get("/api/hosts").json() if h["id"] == "a")


def test_put_snooze_then_the_service_view_carries_it(client):
    until = in_hours(4)
    response = client.put(SHOP_PATH, json={"mode": "snooze", "until": until})
    assert response.status_code == 200
    body = response.json()
    assert (body["scope"], body["mode"]) == ("service", "snooze")
    assert datetime.fromisoformat(body["until"]) == datetime.fromisoformat(until)
    view = shop(client)
    assert view["quiet"] == body
    assert view["health"] == "down"
    assert client.get(f"/api/services/{SHOP}").json()["service"]["quiet"] == body
    assert host_a(client)["quiet"] is None


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"mode": "leave_alone", "until": in_hours(1)}, "until"),
        ({"mode": "snooze", "until": in_hours(-1)}, "until"),
        ({"mode": "snooze"}, "until"),
        ({"mode": "snooze", "until": "2099-01-01T10:00:00"}, "until"),
        ({"mode": "nap"}, "mode"),
    ],
)
def test_a_bad_request_is_422_and_names_the_field(client, body, field):
    response = client.put(SHOP_PATH, json=body)
    assert response.status_code == 422
    assert field in str(response.json()["detail"])
    assert client.get("/api/services").json()[0]["quiet"] is None


def test_leave_alone_with_until_is_422_and_snooze_in_the_past_is_422(client):
    assert (
        client.put(SHOP_PATH, json={"mode": "leave_alone", "until": in_hours(1)}).status_code == 422
    )
    assert client.put(SHOP_PATH, json={"mode": "snooze", "until": in_hours(-1)}).status_code == 422
    assert client.put(SHOP_PATH, json={"mode": "leave_alone"}).status_code == 200


def test_snooze_more_than_seven_days_ahead_is_422(client):
    assert (
        client.put(SHOP_PATH, json={"mode": "snooze", "until": in_hours(24 * 7 + 1)}).status_code
        == 422
    )
    assert (
        client.put(SHOP_PATH, json={"mode": "snooze", "until": in_hours(24 * 7 - 1)}).status_code
        == 200
    )


def test_unknown_service_or_host_is_404(client):
    body = {"mode": "leave_alone"}
    assert client.put("/api/quiet/services/a/compose:nope", json=body).status_code == 404
    assert client.put("/api/quiet/hosts/nope", json=body).status_code == 404


def test_a_quiet_host_marks_every_service_and_lists_them_in_quiet_service_ids(client):
    client.put("/api/quiet/hosts/a", json={"mode": "leave_alone"})
    views = client.get("/api/services").json()
    assert {v["id"] for v in views} == {SHOP, "a/compose:api"}
    assert all(v["quiet"]["scope"] == "host" for v in views)
    host = host_a(client)
    assert host["quiet"]["scope"] == "host"
    assert sorted(host["quiet_service_ids"]) == sorted(v["id"] for v in views)
    assert host["health"] == "down"  # a quiet host keeps its real health


def test_a_host_wins_over_a_service_quiet_on_its_own(client):
    client.put(SHOP_PATH, json={"mode": "leave_alone"})
    client.put("/api/quiet/hosts/a", json={"mode": "snooze", "until": in_hours(1)})
    assert shop(client)["quiet"]["scope"] == "host"
    client.delete("/api/quiet/hosts/a")
    assert shop(client)["quiet"]["scope"] == "service"


def test_a_host_whose_only_down_service_is_quiet_is_ok(client):
    assert host_a(client)["health"] == "down"
    client.put(SHOP_PATH, json={"mode": "leave_alone"})
    host = host_a(client)
    assert host["health"] == "ok"
    assert host["quiet_service_ids"] == [SHOP]
    assert host["quiet"] is None


def test_a_quiet_state_for_a_service_that_is_gone_does_not_break_the_lists(client, parts):
    client.portal.call(parts.quiet.set, "service", "a/compose:gone", "leave_alone", None)
    assert client.get("/api/services").status_code == 200
    host = host_a(client)
    assert host["quiet_service_ids"] == []
    assert host["service_count"] == 2


def test_delete_is_204_twice(client):
    client.put(SHOP_PATH, json={"mode": "leave_alone"})
    assert client.delete(SHOP_PATH).status_code == 204
    assert client.delete(SHOP_PATH).status_code == 204
    assert shop(client)["quiet"] is None
    assert client.delete("/api/quiet/hosts/a").status_code == 204


def test_the_routes_need_a_session(settings, parts):
    with TestClient(create_app(settings, parts)) as anonymous:
        body = {"mode": "leave_alone"}
        assert anonymous.put(SHOP_PATH, json=body).status_code == 401
        assert anonymous.delete(SHOP_PATH).status_code == 401
        assert anonymous.put("/api/quiet/hosts/a", json=body).status_code == 401
        assert anonymous.delete("/api/quiet/hosts/a").status_code == 401
