import asyncio
import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from bleep.api.hosts import container_health, host_health, service_health
from bleep.hosts.config import ServiceOverrides, UrlCheckConfig
from bleep.hosts.registry import Registry
from bleep.incidents.repository import IncidentRepo
from bleep.main import create_app
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.incidents import Signal, Subject
from bleep.watcher.http_checks import UrlCheck
from fakes import FakeEngine, ssh_host

T0 = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)


def c(state="running", health="none", name="c1", exit_code=None, policy=""):
    return ContainerSummary(
        id=f"id-{name}",
        name=name,
        image="img",
        state=state,
        health=health,
        exit_code=exit_code,
        restart_policy=policy,
    )


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
    ("exit_code", "expected"),
    [(0, "stopped"), (143, "stopped"), (1, "down"), (137, "down"), (None, "down")],
)
def test_an_exited_container_is_stopped_only_after_a_clean_exit(exit_code, expected):
    assert container_health(c("exited", exit_code=exit_code)) == expected


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
        # a finished one-shot (exit 0, no restart policy) does not count
        ([c(), c("exited", name="fetch", exit_code=0)], [], "ok"),
        ([c("exited", name="fetch", exit_code=0, policy="no")], [], "stopped"),
        # stopped on purpose, all of it: grey, also with a URL that now fails
        ([c("exited", exit_code=0, policy="always")], [], "stopped"),
        ([c("exited", exit_code=143, policy="unless-stopped")], ["failing"], "stopped"),
        (
            [c("exited", exit_code=0, policy="always"), c("exited", name="c2", exit_code=0)],
            [],
            "stopped",
        ),
        # one stopped among running ones is a partly working service
        ([c(), c("exited", name="db", exit_code=0, policy="always")], [], "degraded"),
        # crashes stay down, one-shot present or not
        ([c("exited", exit_code=1)], [], "down"),
        ([c("exited", exit_code=1), c("exited", name="fetch", exit_code=0)], [], "down"),
        (
            [c("exited", exit_code=0, policy="always"), c("exited", name="c2", exit_code=1)],
            [],
            "down",
        ),
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
        (True, T0, ["stopped"], "ok"),
        (True, T0, ["stopped", "ok"], "ok"),
        (True, T0, ["stopped", "degraded"], "degraded"),
    ],
)
def test_host_health(reachable, last_seen, services, expected):
    assert host_health(reachable, last_seen, services) == expected


@pytest.fixture
def stocked(settings, parts, monkeypatch):
    async def never_answers(*args, **kwargs):
        await asyncio.Event().wait()  # the running watcher must not record a check of its own

    monkeypatch.setattr("bleep.watcher.http_checks.check_url", never_answers)
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
                    restart_count=7,
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
                "exit_code": None,
                "protected": False,
            },
            {
                "id": "2",
                "name": "shop-db-1",
                "image": "pg",
                "state": "restarting",
                "health": "none",
                "restart_count": 7,
                "exit_code": None,
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


def record_check(
    client, parts, check: UrlCheck, age_s: float = 0, host="a", service="a/compose:shop"
):
    at = datetime.now(UTC) - timedelta(seconds=age_s)
    client.portal.call(parts.url_checks.record, host, service, check, at)
    return at


def shop_view(client):
    [shop] = client.get("/api/services", params={"host_id": "a"}).json()
    return shop


def test_a_stored_passing_check_shows_its_result_and_time(stocked, parts):
    at = record_check(stocked, parts, UrlCheck(url="http://a/", ok=True, status=200, latency_ms=42))
    [url] = shop_view(stocked)["urls"]
    assert (url["status"], url["status_code"], url["latency_ms"]) == ("ok", 200, 42)
    assert datetime.fromisoformat(url["last_checked"]) == at


def test_a_failing_check_makes_the_service_down(stocked, parts):
    record_check(
        stocked,
        parts,
        UrlCheck(
            url="http://a/", ok=False, status=500, latency_ms=9, error="expected 200, got 500"
        ),
    )
    shop = shop_view(stocked)
    assert shop["urls"][0]["status"] == "failing"
    assert shop["health"] == "down"
    detail = stocked.get("/api/services/a/compose:shop").json()
    assert detail["service"]["urls"][0]["status"] == "failing"


def test_a_stale_check_is_unknown_but_keeps_its_time(stocked, parts):
    record_check(
        stocked, parts, UrlCheck(url="http://a/", ok=True, status=200, latency_ms=5), age_s=600
    )
    [url] = shop_view(stocked)["urls"]
    assert url["status"] == "unknown"
    assert url["last_checked"] is not None and url["status_code"] == 200


def test_checks_of_other_urls_or_hosts_are_ignored(stocked, parts):
    record_check(stocked, parts, UrlCheck(url="http://gone/", ok=False, latency_ms=1))
    record_check(
        stocked, parts, UrlCheck(url="http://a/", ok=False, latency_ms=1), host="b", service="b/x"
    )
    [url] = shop_view(stocked)["urls"]
    assert (url["status"], url["last_checked"]) == ("unknown", None)


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


def test_services_follow_the_engine_after_a_watcher_tick(settings, parts):
    settings.watch_interval_s = 0.02
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


def test_hosts_carry_basics_when_known(stocked, parts):
    from bleep.schemas.docker import DiskUsage, HostBasics

    entry = parts.registry._entries["a"]
    entry.state = type(entry.state)(
        **{
            **entry.state.__dict__,
            "basics": HostBasics(
                os="Linux",
                disks=[DiskUsage(mount="/", used_percent=49)],
                mem_total=4000,
                mem_available=2000,
                load1=0.4,
                load15=0.3,
                cpus=2,
            ),
        }
    )
    body = {h["id"]: h for h in stocked.get("/api/hosts").json()}
    assert body["a"]["basics"] == {
        "os": "Linux",
        "cpus": 2,
        "mem_total": 4000,
        "mem_available": 2000,
        "load1": 0.4,
        "load15": 0.3,
        "disks": [{"mount": "/", "used_percent": 49.0}],
    }
    assert body["b"]["basics"] is None
