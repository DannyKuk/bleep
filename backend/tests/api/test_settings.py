"""Settings and plugins over HTTP: precedence, and no secret in either direction (#36)."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from bleep.guard.plan_check import check_plan
from bleep.hosts.config import ServiceOverrides, UrlCheckConfig
from bleep.hosts.registry import Registry
from bleep.main import create_app
from bleep.memory.embeddings import FakeEmbedder
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.graph import PlanStep, Proposal
from bleep.tools.registry import ToolContext
from bleep.wiring import build_parts
from fakes import COOLIFY_TOKEN, FakeCoolifyPlugin, FakeEngine, socket_host

T0 = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
FILE_URL = "https://shop.example.test/health"
DB_URL = "https://shop.example.test/ready"
OPENROUTER_KEY = "sk-or-v1-env-key-0123456789"
SHOP_URLS = {"hosts": {"a": {"services": {"compose:shop": {"urls": [{"url": DB_URL}]}}}}}


@pytest.fixture
def app(settings, monkeypatch):
    """A logged-in client over host `a` (the `shop` service with a URL in the hosts file) with
    the fake Coolify plugin, its token and a model key in the env."""
    monkeypatch.setenv("COOLIFY_TOKEN", COOLIFY_TOKEN)
    monkeypatch.setenv("OPENROUTER_API_KEY", OPENROUTER_KEY)
    host = socket_host(
        id="a",
        services={"compose:shop": ServiceOverrides(urls=[UrlCheckConfig(url=FILE_URL)])},
    )
    web = ContainerSummary(
        id="id-web",
        name="shop-web-1",
        image="shop:1",
        state="running",
        labels={"com.docker.compose.project": "shop"},
    )

    async def connect(_host):
        return None, FakeEngine("a", [web])

    registry = Registry([host], connect, clock=lambda: T0)
    parts = build_parts(settings, registry, FakeEmbedder(), plugins=[FakeCoolifyPlugin()])
    with TestClient(create_app(settings, parts)) as client:
        assert client.post("/api/auth/login", json={"password": "test-password"}).status_code == 204
        client.parts = parts  # type: ignore[attr-defined]
        yield client


def test_get_shows_the_values_in_force_with_no_override(app):
    body = app.get("/api/settings").json()
    assert body["budgets"]["investigate_tool_calls"] == 8
    assert body["models"]["model"] == "openai/gpt-6-luna"
    assert body["hosts"][0]["services"]["compose:shop"]["urls"][0]["url"] == FILE_URL
    assert body["plugins"] == {"coolify": False}
    assert body["overrides"]["hosts"] == {}


def test_a_database_url_wins_over_the_hosts_file(app):
    response = app.put("/api/settings", json=SHOP_URLS)
    assert response.status_code == 200
    assert response.json()["hosts"][0]["services"]["compose:shop"]["urls"][0]["url"] == DB_URL
    services = app.get("/api/services").json()
    assert [u["url"] for u in services[0]["urls"]] == [DB_URL]


def test_a_section_left_out_is_kept(app):
    app.put("/api/settings", json={"budgets": {"daily_cost_usd": 0.5}})
    app.put("/api/settings", json={"thresholds": {"disk_percent": 80}})
    body = app.get("/api/settings").json()
    assert body["budgets"]["daily_cost_usd"] == 0.5
    assert body["thresholds"]["disk_percent"] == 80
    assert body["overrides"]["budgets"] == {"daily_cost_usd": 0.5}


SECRET_BODIES = [
    {"token": "abc"},
    {"TOKEN": "abc"},
    {"budgets": {"apiKey": "abc"}},
    {"models": {"openrouter_api_key": "abc"}},
    {"models": {"OPENROUTER-API-KEY": "abc"}},
    {"hosts": {"a": {"password": "abc"}}},
    {"hosts": {"a": {"services": {"compose:shop": {"Db_Password": "abc"}}}}},
    {
        "hosts": {
            "a": {"services": {"compose:shop": {"urls": [{"url": "https://x.test", "key": "abc"}]}}}
        }
    },
    {"hosts": {"a": {"rules": ["deploy with token ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789"]}}},
    {
        "hosts": {
            "a": {
                "services": {"compose:shop": {"urls": [{"url": "https://u:hunter2pass@x.test/"}]}}
            }
        }
    },
    {"hosts": {"a": {"services": {"x-api-key": {}}}}},
    {"github": {"token": {"env": "GITHUB_TOKEN"}}},
]


@pytest.mark.parametrize("body", SECRET_BODIES)
def test_a_settings_put_carrying_a_secret_is_refused_without_echoing_it(app, body):
    response = app.put("/api/settings", json=body)
    assert response.status_code == 422
    for word in ("abc", "ghp_", "hunter2", "GITHUB_TOKEN"):
        assert word not in str(response.json()["detail"])
    assert app.get("/api/settings").json()["overrides"] == {"hosts": {}}


def test_an_invalid_body_is_refused_with_reasons_and_without_its_values(app):
    response = app.put("/api/settings", json={"budgets": {"daily_cost_usd": "lots-and-lots"}})
    assert response.status_code == 422
    assert "lots-and-lots" not in response.text
    assert response.json()["detail"][0]["loc"] == ["budgets", "daily_cost_usd"]


def test_an_unknown_host_is_refused(app):
    response = app.put("/api/settings", json={"hosts": {"ghost": {"rules": ["x"]}}})
    assert response.status_code == 422
    assert "ghost" in response.json()["detail"]


def test_settings_never_show_a_model_key(app):
    app.put("/api/settings", json={"models": {"model": "openai/other"}})
    response = app.get("/api/settings")
    assert response.json()["models"]["model"] == "openai/other"
    assert OPENROUTER_KEY not in response.text
    assert "api_key" not in response.text


def test_plugins_list_settings_and_whether_secrets_are_set_never_the_value(app):
    response = app.put(
        "/api/plugins/coolify",
        json={"enabled": True, "settings": {"url": "https://coolify.example.test"}},
    )
    assert response.status_code == 200
    listed = app.get("/api/plugins")
    assert COOLIFY_TOKEN not in listed.text  # the fake's health check echoes it
    assert COOLIFY_TOKEN not in response.text
    (plugin,) = listed.json()
    assert plugin["enabled"] is True
    assert plugin["secrets"] == [{"env": "COOLIFY_TOKEN", "set": True}]
    assert plugin["settings"] == {"url": "https://coolify.example.test", "team": "main"}
    assert plugin["health"]["ok"] is True


@pytest.mark.parametrize(
    "body",
    [
        {"settings": {"url": "https://c.test", "token": "abc"}},
        {"settings": {"url": "https://c.test", "ApiKey": "abc"}},
        {"settings": {"url": "https://c.test", "nested": {"password": "abc"}}},
        {"settings": {"url": "https://user:abcdefgh1234@c.test"}},
        {"enabled": True, "COOLIFY_TOKEN": "abc"},
    ],
)
def test_a_plugin_put_carrying_a_secret_is_refused(app, body):
    response = app.put("/api/plugins/coolify", json=body)
    assert response.status_code == 422
    assert "abc" not in response.text
    assert app.get("/api/plugins").json()[0]["enabled"] is False


def test_a_plugin_put_with_unknown_settings_is_refused(app):
    response = app.put("/api/plugins/coolify", json={"settings": {"url": "x", "surprise": 1}})
    assert response.status_code == 422


def test_switching_on_without_the_secret_is_refused(app, monkeypatch):
    monkeypatch.delenv("COOLIFY_TOKEN")
    response = app.put(
        "/api/plugins/coolify", json={"enabled": True, "settings": {"url": "https://c.test"}}
    )
    assert response.status_code == 422
    assert "COOLIFY_TOKEN" in response.json()["detail"]


def test_unknown_plugin(app):
    assert app.put("/api/plugins/nope", json={"enabled": True}).status_code == 404


def test_switching_a_plugin_off_reaches_the_next_incident(app):
    """Its read tools leave the graph's catalogue, and its action fails the plan check and has
    no act handler, for a plan approved while it was on as well."""
    deps = app.parts.graph_deps
    ctx = ToolContext(incident_id="i1", host_id="a", registry=deps.registry)
    step = PlanStep(action="coolify.redeploy", host_id="a", target="shop")
    plan = Proposal(kind="act", steps=[step], reason="the release broke")

    def rules() -> set[str]:
        enabled = deps.settings_store.enabled_actions()
        return {
            v.rule for v in check_plan(plan, deps.registry, enabled, "a", incident_containers=[])
        }

    app.put("/api/plugins/coolify", json={"enabled": True, "settings": {"url": "https://c.test"}})
    assert "coolify_applications" in {t.name for t in deps.tools.read_tools(ctx)}
    assert "disabled_action" not in rules()
    assert deps.act_tools.get("coolify.redeploy") is not None

    app.put("/api/plugins/coolify", json={"enabled": False})
    assert "coolify_applications" not in {t.name for t in deps.tools.read_tools(ctx)}
    assert "disabled_action" in rules()
    assert deps.act_tools.get("coolify.redeploy") is None


def test_settings_reach_the_graph(app):
    app.put("/api/settings", json={"budgets": {"investigate_tool_calls": 3}})
    assert app.parts.graph_deps.settings_store.budgets().investigate_tool_calls == 3
