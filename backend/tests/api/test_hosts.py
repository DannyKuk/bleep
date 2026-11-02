import json
import time
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from bleep.api.hosts import container_health, host_health, service_health
from bleep.hosts.config import ServiceOverrides, UrlCheckConfig
from bleep.hosts.registry import Registry
from bleep.incidents.repository import IncidentRepo
from bleep.main import create_app
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.incidents import Signal, Subject
from fakes import FakeEngine, ssh_host

T0 = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)


def c(state="running", health="none", name="c1"):
    return ContainerSummary(id=f"id-{name}", name=name, image="img", state=state, health=health)


@pytest.mark.parametrize(
    ("state", "health", "expected"),
    [
        ("running", "none", "ok"),
        ("running", "healthy", "ok"),
        ("running", "starting", "degraded"),
        ("restarting", "none", "degraded"),
        ("running", "unhealthy", "down"),
        ("exited", "none", "down"),
        ("dead", "none", "down"),
        ("created", "none", "down"),
        ("paused", "none", "down"),
        ("removing", "none", "unknown"),
    ],
)
def test_container_health_table(state, health, expected):
    assert container_health(c(state, health)) == expected


@pytest.mark.parametrize(
    ("containers", "urls", "expected"),
    [
        ([], [], "unknown"),
        ([c()], [], "ok"),
        ([c(), c("restarting", name="c2")], [], "degraded"),
        ([c("restarting", name="c2"), c("exited")], [], "down"),
        ([c()], ["unknown"], "ok"),
        ([c()], ["ok"], "ok"),
        ([c()], ["failing"], "down"),
        ([c("restarting")], ["failing"], "down"),
        ([], ["failing"], "down"),
        ([], ["unknown"], "unknown"),
    ],
)
def test_service_health_is_the_worst_of_containers_and_urls(containers, urls, expected):
    assert service_health(containers, urls) == expected


@pytest.mark.parametrize(
    ("reachable", "last_seen", "services", "expected"),
    [
        (False, None, [], "unknown"),
        (False, T0, ["ok"], "down"),
        (True, T0, [], "ok"),
        (True, T0, ["ok", "ok"], "ok"),
        (True, T0, ["ok", "degraded"], "degraded"),
        (True, T0, ["degraded", "down", "ok"], "down"),
        (True, T0, ["unknown"], "unknown"),
    ],
)
def test_host_health(reachable, last_seen, services, expected):
    assert host_health(reachable, last_seen, services) == expected


@pytest.fixture
def stocked(settings, parts):
    hosts = [
        ssh_host(
            id="a",
            name="Host A",
            services={
                "shop": ServiceOverrides(
                    urls=[UrlCheckConfig(url="http://a/")], protected_containers=["shop-db-1"]
                )
            },
        ),
        ssh_host(id="b", name="Host B"),
    ]
    engines = {
        "a": FakeEngine(
            "a",
            [
                ContainerSummary(
                    id="1",
                    name="shop-web-1",
                    image="shop:1",
                    state="running",
                    health="healthy",
                    labels={"com.docker.compose.project": "shop"},
                ),
                ContainerSummary(
                    id="2",
                    name="shop-db-1",
                    image="pg",
                    state="restarting",
                    labels={"com.docker.compose.project": "shop", "SECRET": "hunter2"},
                ),
            ],
        ),
        "b": FakeEngine("b", [c(name="redis")]),
    }

    async def connect(host):
        return None, engines[host.id]

    parts.registry = Registry(hosts, connect, clock=lambda: T0)
    with TestClient(create_app(settings, parts)) as client:
        client.post("/api/auth/login", json={"password": "test-password"})
        yield client


def test_hosts(stocked):
    body = stocked.get("/api/hosts").json()
    assert body == [
        {
            "id": "a",
            "name": "Host A",
            "kind": "ssh",
            "reachable": True,
            "last_seen": "2026-11-02T10:00:00Z",
            "error": None,
            "basics": None,
            "service_count": 1,
            "health": "degraded",
        },
        {
            "id": "b",
            "name": "Host B",
            "kind": "ssh",
            "reachable": True,
            "last_seen": "2026-11-02T10:00:00Z",
            "error": None,
            "basics": None,
            "service_count": 1,
            "health": "ok",
        },
    ]


def test_services_shape(stocked):
    shop = stocked.get("/api/services", params={"host_id": "a"}).json()
    assert len(shop) == 1
    assert shop[0] == {
        "id": "a/compose:shop",
        "host_id": "a",
        "name": "shop",
        "source": "compose",
        "health": "degraded",
        "containers": [
            {
                "id": "1",
                "name": "shop-web-1",
                "image": "shop:1",
                "state": "running",
                "health": "healthy",
                "restart_count": 0,
                "protected": False,
            },
            {
                "id": "2",
                "name": "shop-db-1",
                "image": "pg",
                "state": "restarting",
                "health": "none",
                "restart_count": 0,
                "protected": True,
            },
        ],
        "urls": [
            {
                "url": "http://a/",
                "status": "unknown",
                "last_checked": None,
                "status_code": None,
                "latency_ms": None,
            }
        ],
        "open_incident_id": None,
    }


def test_services_without_filter_lists_all_hosts(stocked):
    assert {s["host_id"] for s in stocked.get("/api/services").json()} == {"a", "b"}


def test_unknown_host_filter_is_empty(stocked):
    assert stocked.get("/api/services", params={"host_id": "zzz"}).json() == []


async def test_open_incident_id_comes_from_the_repo(settings, parts):
    engine = FakeEngine("a", [c(name="solo")])

    async def connect(host):
        return None, engine

    parts.registry = Registry([ssh_host(id="a")], connect)
    await parts.db.migrate()
    subject = Subject(kind="service", host_id="a", service_id="a/container:solo")
    incident, _ = await IncidentRepo(parts.db).open_or_attach(
        subject, [Signal(subject=subject, kind="container_died", at=T0)]
    )
    with TestClient(create_app(settings, parts)) as client:
        client.post("/api/auth/login", json={"password": "test-password"})
        [service] = client.get("/api/services").json()
    assert service["open_incident_id"] == incident.id


def test_unreachable_host_without_snapshot_is_unknown(settings, parts):
    async def connect(host):
        raise OSError("refused")

    parts.registry = Registry([ssh_host(id="a")], connect)
    with TestClient(create_app(settings, parts)) as client:
        client.post("/api/auth/login", json={"password": "test-password"})
        [host] = client.get("/api/hosts").json()
    assert host["reachable"] is False
    assert host["health"] == "unknown"
    assert host["error"] == "refused"
    assert host["last_seen"] is None


def test_no_env_values_or_labels_in_any_response(stocked):
    text = stocked.get("/api/services").text + stocked.get("/api/hosts").text
    assert "hunter2" not in text
    assert '"env"' not in text
    assert "labels" not in json.loads(stocked.get("/api/services").text)[0]["containers"][0]


def test_openapi_exposes_the_view_types(stocked):
    schemas = stocked.get("/openapi.json").json()["components"]["schemas"]
    assert {"HostView", "ServiceView", "ContainerView", "UrlStatus"} <= set(schemas)


def test_services_follow_the_engine_after_a_refresh_tick(settings, parts, monkeypatch):
    monkeypatch.setattr("bleep.main.REFRESH_INTERVAL_S", 0.02)
    engine = FakeEngine("a", [c(name="web")])

    async def connect(host):
        return None, engine

    parts.registry = Registry([ssh_host(id="a")], connect, clock=lambda: T0)
    with TestClient(create_app(settings, parts)) as client:
        client.post("/api/auth/login", json={"password": "test-password"})
        assert [s["name"] for s in client.get("/api/services").json()] == ["web"]
        engine.container_list.append(c(name="worker"))
        names = []
        for _ in range(100):
            names = sorted(s["name"] for s in client.get("/api/services").json())
            if names == ["web", "worker"]:
                break
            time.sleep(0.02)
    assert names == ["web", "worker"]
