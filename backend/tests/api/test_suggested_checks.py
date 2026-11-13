"""A Coolify application's domains offered as its service's URL checks, and accepted through the
service page's own save (#348). Coolify answers from the recorded `domains.json`; no network."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bleep.hosts.config import ServiceOverrides, UrlCheckConfig
from bleep.hosts.registry import Registry
from bleep.main import create_app
from bleep.memory.embeddings import FakeEmbedder
from bleep.plugins.coolify.plugin import CoolifyPlugin
from bleep.schemas.docker import ContainerSummary
from bleep.wiring import build_parts
from fakes import CoolifyAnswers, FakeEngine, compose_labels, socket_host

T0 = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)
FIXTURES = Path(__file__).parent.parent / "fixtures" / "coolify"
SHOP = "shop0uuid0000000000000a1"
SHOP_ID = f"a/{SHOP}"
HAD = "https://example.com/shop/health"
SUGGESTED = ["https://shop.example.com/health", "https://WWW.shop.example.com/health"]


def domains() -> object:
    return json.loads((FIXTURES / "domains.json").read_text(encoding="utf-8"))


def put_plugin(client: TestClient, on: bool):
    listed = {p["name"]: p for p in client.get("/api/plugins").json()}
    version = listed["coolify"]["version"]
    return client.put(
        "/api/plugins/coolify", json={"enabled": on}, headers={"If-Match": f'"{version}"'}
    )


@pytest.fixture
def coolify() -> CoolifyAnswers:
    return CoolifyAnswers(200, domains())


@pytest.fixture
def app(settings, monkeypatch, coolify):
    """A logged-in client over host `a`, running Coolify's `shop` (which the hosts file checks at
    one of its domains) and a Compose project, with the real Coolify plugin over `coolify`."""
    monkeypatch.setenv("COOLIFY_URL", "https://coolify.example.com")
    monkeypatch.setenv("COOLIFY_TOKEN", "cool-token-for-tests")
    host = socket_host(id="a", services={SHOP: ServiceOverrides(urls=[UrlCheckConfig(url=HAD)])})
    labels = {
        "coolify.managed": "true",
        "coolify.type": "application",
        "coolify.applicationUuid": SHOP,
        "coolify.resourceName": "shop",
        "coolify.pullRequestId": "0",
    }
    containers = [
        ContainerSummary(
            id="1", name=f"{SHOP}-0745", image="shop:1", state="running", labels=labels
        ),
        ContainerSummary(
            id="2",
            name="blog-web-1",
            image="blog:1",
            state="running",
            labels=compose_labels("blog"),
        ),
    ]

    async def connect(_host):
        return None, FakeEngine("a", containers)

    registry = Registry([host], connect, clock=lambda: T0)
    parts = build_parts(
        settings, registry, FakeEmbedder(), plugins=[CoolifyPlugin(transport=coolify)]
    )
    with TestClient(create_app(settings, parts)) as client:
        assert client.post("/api/auth/login", json={"password": "test-password"}).status_code == 204
        yield client


def suggested(client: TestClient, service_id: str = SHOP_ID):
    return client.get(f"/api/services/{service_id}/suggested-checks")


def test_nothing_is_suggested_and_coolify_is_not_asked_while_the_plugin_is_off(app, coolify):
    assert app.get(f"/api/services/{SHOP_ID}").status_code == 200  # the service is there
    response = suggested(app)
    assert response.status_code == 200
    assert response.json() == {"offered": [], "stale": []}
    assert coolify.requests == []


def test_the_apps_domains_are_suggested_without_the_check_it_has(app):
    assert put_plugin(app, True).status_code == 200
    body = suggested(app).json()["offered"]
    assert [check["url"] for check in body] == SUGGESTED
    assert body[0] == {"url": SUGGESTED[0], "expect_status": 204, "body_contains": None}


def test_a_service_not_run_by_coolify_is_offered_nothing_and_an_unknown_one_is_404(app):
    put_plugin(app, True)
    assert suggested(app, "a/compose:blog").json() == {"offered": [], "stale": []}
    assert suggested(app, "a/nothing-here").status_code == 404


def test_coolify_failing_suggests_nothing_and_never_shows_its_answer(app, coolify, caplog):
    put_plugin(app, True)
    coolify.status, coolify.body = 500, {"message": "stack trace with a secret"}
    with caplog.at_level(logging.DEBUG):
        response = suggested(app)
    assert response.status_code == 200
    assert response.json() == {"offered": [], "stale": []}
    assert "stack trace with a secret" not in caplog.text


def test_accepted_suggestions_are_stored_as_service_page_checks_and_not_offered_again(app):
    put_plugin(app, True)
    offered = suggested(app).json()["offered"]
    view = app.get("/api/settings").json()
    in_force = view["hosts"][0]["services"][SHOP]["urls"]
    body = {"hosts": {"a": {"services": {SHOP: {"urls": in_force + offered}}}}}
    saved = app.put("/api/settings", json=body, headers={"If-Match": f'"{view["version"]}"'})
    assert saved.status_code == 200

    stored = saved.json()["overrides"]["hosts"]["a"]["services"][SHOP]["urls"]
    assert [u["url"] for u in stored] == [HAD, *SUGGESTED]
    shown = app.get(f"/api/services/{SHOP_ID}").json()
    assert [u["url"] for u in shown["service"]["urls"]] == [HAD, *SUGGESTED]
    assert suggested(app).json()["offered"] == []


GONE = "https://old.shop.example.com/health"


def save_checks(client: TestClient, urls: list[str]):
    view = client.get("/api/settings").json()
    checks = [{"url": url} for url in urls]
    body = {"hosts": {"a": {"services": {SHOP: {"urls": checks}}}}}
    saved = client.put("/api/settings", json=body, headers={"If-Match": f'"{view["version"]}"'})
    assert saved.status_code == 200


def test_a_check_on_a_domain_the_app_dropped_is_flagged_and_nothing_is_removed(app):
    save_checks(app, [HAD, GONE])
    put_plugin(app, True)
    body = suggested(app).json()
    assert [check["url"] for check in body["stale"]] == [GONE]
    assert [check["url"] for check in body["offered"]] == SUGGESTED

    shown = app.get(f"/api/services/{SHOP_ID}").json()
    assert [u["url"] for u in shown["service"]["urls"]] == [HAD, GONE]


def test_nothing_is_flagged_while_the_plugin_is_off(app, coolify):
    save_checks(app, [HAD, GONE])
    assert suggested(app).json() == {"offered": [], "stale": []}
    assert coolify.requests == []


def test_a_flagged_check_goes_with_the_services_own_save_and_the_offer_stays(app):
    save_checks(app, [HAD, GONE])
    put_plugin(app, True)
    save_checks(app, [HAD])
    body = suggested(app).json()
    assert body["stale"] == []
    assert [check["url"] for check in body["offered"]] == SUGGESTED
