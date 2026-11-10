"""Settings and plugins over HTTP: precedence, and no secret in either direction (#36)."""

import asyncio
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from bleep.guard.plan_check import check_plan
from bleep.hosts.config import ServiceOverrides, UrlCheckConfig
from bleep.hosts.registry import Registry
from bleep.main import create_app
from bleep.memory.embeddings import FakeEmbedder
from bleep.plugins.base import Plugin, PluginContext
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.graph import PlanStep, Proposal
from bleep.schemas.plugins import PluginHealth
from bleep.tools.registry import ToolContext, ToolSpec
from bleep.wiring import build_parts
from fakes import COOLIFY_TOKEN, FakeCoolifyPlugin, FakeEngine, compose_labels, socket_host

T0 = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
FILE_URL = "https://shop.example.test/health"
DB_URL = "https://shop.example.test/ready"
OPENROUTER_KEY = "sk-or-v1-env-key-0123456789"


class NoSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")


SHOP_URLS = {"hosts": {"a": {"services": {"compose:shop": {"urls": [{"url": DB_URL}]}}}}}


def put_settings(client: TestClient, body: object, version: str | None = None):
    """`PUT /api/settings` built from the version read just before, unless one is given."""
    if version is None:
        version = client.get("/api/settings").json()["version"]
    return client.put("/api/settings", json=body, headers={"If-Match": f'"{version}"'})


def put_plugin(client: TestClient, name: str, json: object, version: str | None = None):
    """`PUT /api/plugins/{name}` built from the plugin as read just before, unless given."""
    if version is None:
        listed = {p["name"]: p for p in client.get("/api/plugins").json()}
        version = listed[name]["version"] if name in listed else "none"
    return client.put(f"/api/plugins/{name}", json=json, headers={"If-Match": f'"{version}"'})


@pytest.fixture
def app(settings, monkeypatch):
    """A logged-in client over host `a` (the `shop` service with a URL in the hosts file) with
    the fake Coolify plugin, its token and a model key in the env, and the dev panel on, which
    edits the models and the budgets."""
    settings.dev_panel = True
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
        labels=compose_labels("shop"),
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
    response = put_settings(app, SHOP_URLS)
    assert response.status_code == 200
    assert response.json()["hosts"][0]["services"]["compose:shop"]["urls"][0]["url"] == DB_URL
    services = app.get("/api/services").json()
    assert [u["url"] for u in services[0]["urls"]] == [DB_URL]


def test_a_section_left_out_is_kept(app):
    put_settings(app, {"budgets": {"daily_cost_usd": 0.5}})
    put_settings(app, {"thresholds": {"disk_percent": 80}})
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
    response = put_settings(app, body)
    assert response.status_code == 422
    for word in ("abc", "ghp_", "hunter2", "GITHUB_TOKEN"):
        assert word not in str(response.json()["detail"])
    assert app.get("/api/settings").json()["overrides"] == {"hosts": {}}


def test_a_body_over_the_size_cap_is_a_413_before_the_secret_check(app, monkeypatch):
    def never(_body):
        raise AssertionError("the secret check must not read a body over the cap")

    monkeypatch.setattr("bleep.api.settings.check_body", never)
    rules = ["never restart postgres without asking"] * 3_000
    response = put_settings(app, {"hosts": {"a": {"rules": rules}}})
    assert response.status_code == 413
    assert "100,000" in response.json()["detail"]


def test_an_invalid_body_is_refused_with_reasons_and_without_its_values(app):
    response = put_settings(app, {"budgets": {"daily_cost_usd": "lots-and-lots"}})
    assert response.status_code == 422
    assert "lots-and-lots" not in response.text
    assert response.json()["detail"][0]["loc"] == ["budgets", "daily_cost_usd"]


@pytest.mark.parametrize(
    "body",
    [
        {"budgets": {"daily_cost_usd": 50}},
        {"budgets": {}},
        {"thresholds": {"disk_percent": 80}, "budgets": {}},
    ],
)
def test_with_the_dev_panel_off_the_budgets_are_refused(settings, app, body):
    settings.dev_panel = False
    before = app.get("/api/settings").json()
    response = put_settings(app, body)
    assert response.status_code == 403
    assert "BLEEP_DEV_PANEL" in response.json()["detail"]
    after = app.get("/api/settings").json()
    assert (after["overrides"], after["version"]) == (before["overrides"], before["version"])


def test_with_the_dev_panel_off_the_other_sections_are_still_saved(settings, app):
    settings.dev_panel = False
    response = put_settings(app, {"thresholds": {"disk_percent": 80}, "budgets": None})
    assert response.status_code == 200
    assert response.json()["thresholds"]["disk_percent"] == 80


def test_with_the_dev_panel_off_the_models_are_saved_from_the_settings_page(settings, app):
    """The model choice moved to the settings page (#372)."""
    settings.dev_panel = False
    response = put_settings(app, {"models": {"model": "openai/other"}})
    assert response.status_code == 200
    assert response.json()["models"]["model"] == "openai/other"


def test_with_the_dev_panel_off_a_secret_is_still_refused_as_one(settings, app):
    settings.dev_panel = False
    response = put_settings(app, {"models": {"openrouter_api_key": "abc"}})
    assert response.status_code == 422


def test_the_time_zone_is_chosen_with_the_dev_panel_off_and_cleared_back_to_none(settings, app):
    settings.dev_panel = False
    assert app.get("/api/settings").json()["timezone"] is None

    response = put_settings(app, {"general": {"timezone": "Europe/Vienna"}})
    assert response.status_code == 200
    body = response.json()
    assert (body["timezone"], body["overrides"]["general"]) == (
        "Europe/Vienna",
        {"timezone": "Europe/Vienna"},
    )
    assert app.parts.settings_store.zone() == ZoneInfo("Europe/Vienna")

    cleared = put_settings(app, {"general": {}}).json()
    assert cleared["timezone"] is None and not cleared["overrides"].get("general")


@pytest.mark.parametrize("name", ["Mars/Olympus", "../../etc/passwd", "Europe", "CET+5"])
def test_an_unknown_time_zone_is_refused_and_nothing_changes(app, name):
    before = app.get("/api/settings").json()
    response = put_settings(app, {"general": {"timezone": name}})
    assert response.status_code == 422
    [problem] = response.json()["detail"]
    assert problem["loc"] == ["general", "timezone"]
    assert "IANA time zone" in problem["msg"] and f'"{name}"' not in response.text
    after = app.get("/api/settings").json()
    assert (after["timezone"], after["version"]) == (None, before["version"])


def test_the_language_is_english_by_default_and_german_is_saved_beside_the_time_zone(app):
    assert app.get("/api/settings").json()["language"] == "en"

    response = put_settings(app, {"general": {"timezone": "Europe/Vienna", "language": "de"}})
    assert response.status_code == 200
    body = response.json()
    assert (body["language"], body["timezone"]) == ("de", "Europe/Vienna")
    assert body["overrides"]["general"] == {"timezone": "Europe/Vienna", "language": "de"}
    assert app.parts.graph_deps.settings_store.language() == "de"  # what the graph reads

    cleared = put_settings(app, {"general": {"timezone": "Europe/Vienna"}}).json()
    assert (cleared["language"], cleared["timezone"]) == ("en", "Europe/Vienna")


@pytest.mark.parametrize("language", ["fr", "DE", "", "deutsch"])
def test_a_language_other_than_english_or_german_is_refused_and_nothing_changes(app, language):
    before = app.get("/api/settings").json()
    response = put_settings(app, {"general": {"language": language}})
    assert response.status_code == 422
    [problem] = response.json()["detail"]
    assert problem["loc"] == ["general", "language"]
    after = app.get("/api/settings").json()
    assert (after["language"], after["version"]) == ("en", before["version"])


def test_an_unknown_host_is_refused(app):
    response = put_settings(app, {"hosts": {"ghost": {"rules": ["x"]}}})
    assert response.status_code == 422
    assert "ghost" in response.json()["detail"]


def test_settings_never_show_a_model_key(app):
    put_settings(app, {"models": {"model": "openai/other"}})
    response = app.get("/api/settings")
    assert response.json()["models"]["model"] == "openai/other"
    assert OPENROUTER_KEY not in response.text
    assert "api_key" not in response.text


def test_settings_say_whether_the_model_key_is_set_by_its_variable(app):
    assert app.get("/api/settings").json()["model_key"] == {
        "env": "OPENROUTER_API_KEY",
        "file": None,
        "set": True,
        "source": "env",
    }


def test_settings_say_when_the_model_key_is_missing(settings, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    parts = build_parts(settings, Registry([]), FakeEmbedder())
    with TestClient(create_app(settings, parts)) as client:
        client.post("/api/auth/login", json={"password": "test-password"})
        assert client.get("/api/settings").json()["model_key"]["set"] is False


def test_settings_show_the_hosts_connection_and_which_protection_is_fixed(app):
    host = app.get("/api/settings").json()["hosts"][0]
    assert host["connection"]["kind"] == "socket"
    assert host["connection"]["endpoint"] == "unix:///var/run/docker.sock"
    assert host["services"]["compose:shop"]["hosts_file_protected_containers"] == []


def test_plugins_list_settings_and_whether_secrets_are_set_never_the_value(app):
    response = put_plugin(
        app,
        "coolify",
        json={"enabled": True, "settings": {"url": "https://coolify.example.test"}},
    )
    assert response.status_code == 200
    listed = app.get("/api/plugins")
    assert COOLIFY_TOKEN not in listed.text  # the fake's health check echoes it
    assert COOLIFY_TOKEN not in response.text
    (plugin,) = listed.json()
    assert plugin["enabled"] is True
    assert plugin["secrets"] == [{"env": "COOLIFY_TOKEN", "set": True, "required": True}]
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
    response = put_plugin(app, "coolify", json=body)
    assert response.status_code == 422
    assert "abc" not in response.text
    assert app.get("/api/plugins").json()[0]["enabled"] is False


def test_a_plugin_put_with_unknown_settings_is_refused(app):
    response = put_plugin(app, "coolify", json={"settings": {"url": "x", "surprise": 1}})
    assert response.status_code == 422


def test_switching_on_without_the_secret_is_refused(app, monkeypatch):
    monkeypatch.delenv("COOLIFY_TOKEN")
    response = put_plugin(
        app, "coolify", json={"enabled": True, "settings": {"url": "https://c.test"}}
    )
    assert response.status_code == 422
    assert "COOLIFY_TOKEN" in response.json()["detail"]


def test_a_plugin_setting_refused_says_why_for_each_field(app):
    response = put_plugin(app, "coolify", json={"settings": {"team": 7}})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "url: Field required" in detail
    assert "team: Input should be a valid string" in detail


def test_a_plugin_switched_on_without_its_secret_after_a_restart_shows_it_and_switches_off(
    settings, monkeypatch
):
    monkeypatch.setenv("COOLIFY_TOKEN", COOLIFY_TOKEN)
    parts = build_parts(settings, Registry([]), FakeEmbedder(), plugins=[FakeCoolifyPlugin()])
    with TestClient(create_app(settings, parts)) as client:
        client.post("/api/auth/login", json={"password": "test-password"})
        on = {"enabled": True, "settings": {"url": "https://c.test"}}
        assert put_plugin(client, "coolify", json=on).status_code == 200

    monkeypatch.delenv("COOLIFY_TOKEN")
    parts = build_parts(settings, Registry([]), FakeEmbedder(), plugins=[FakeCoolifyPlugin()])
    with TestClient(create_app(settings, parts)) as client:
        client.post("/api/auth/login", json={"password": "test-password"})
        (plugin,) = client.get("/api/plugins").json()
        assert (plugin["enabled"], plugin["switched_on"]) == (False, True)
        assert plugin["secrets"] == [{"env": "COOLIFY_TOKEN", "set": False, "required": True}]

        off = put_plugin(client, "coolify", json={"enabled": False})
        assert off.status_code == 200
        assert (off.json()["enabled"], off.json()["switched_on"]) == (False, False)


def test_plugin_health_checks_run_together(settings):
    """Each check waits until every other one has started: one after another, the first would
    time out."""
    started: list[str] = []
    everyone = asyncio.Event()

    class Waiting(Plugin):
        title = "Waiting"
        settings_model = NoSettings

        def tools(self, ctx: PluginContext) -> list[ToolSpec]:
            return []

        async def health(self, ctx: PluginContext) -> PluginHealth:
            started.append(self.name)
            if len(started) == 3:
                everyone.set()
            await asyncio.wait_for(everyone.wait(), 2)
            return PluginHealth(ok=True, detail="up")

    waiting = [type(name, (Waiting,), {"name": name})() for name in ("one", "two", "three")]
    parts = build_parts(settings, Registry([]), FakeEmbedder(), plugins=waiting)
    with TestClient(create_app(settings, parts)) as client:
        client.post("/api/auth/login", json={"password": "test-password"})
        for plugin in waiting:
            assert put_plugin(client, plugin.name, json={"enabled": True}).status_code == 200
        started.clear()
        everyone.clear()
        listed = client.get("/api/plugins").json()
    assert [p["name"] for p in listed] == ["one", "two", "three"]
    assert [p["health"] for p in listed] == [{"ok": True, "detail": "up"}] * 3


def test_unknown_plugin(app):
    assert put_plugin(app, "nope", json={"enabled": True}).status_code == 404


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

    put_plugin(app, "coolify", json={"enabled": True, "settings": {"url": "https://c.test"}})
    assert "coolify_applications" in {t.name for t in deps.tools.read_tools(ctx)}
    assert "disabled_action" not in rules()
    assert deps.act_tools.get("coolify.redeploy") is not None

    put_plugin(app, "coolify", json={"enabled": False})
    assert "coolify_applications" not in {t.name for t in deps.tools.read_tools(ctx)}
    assert "disabled_action" in rules()
    assert deps.act_tools.get("coolify.redeploy") is None


def test_settings_reach_the_graph(app):
    put_settings(app, {"budgets": {"investigate_tool_calls": 3}})
    assert app.parts.graph_deps.settings_store.budgets().investigate_tool_calls == 3


def test_a_bad_url_is_refused_without_echoing_it(app):
    url = "https://shop.example.test:hunter2xyz/health"
    body = {"hosts": {"a": {"services": {"compose:shop": {"urls": [{"url": url}]}}}}}
    response = put_settings(app, body)
    assert response.status_code == 422
    assert "hunter2xyz" not in response.text


def protect(name: str) -> dict:
    return {"hosts": {"a": {"services": {"compose:shop": {"protected_containers": [name]}}}}}


@pytest.mark.parametrize("name", ["Shop API", "name:main"])
def test_a_protected_entry_with_spaces_or_a_colon_is_accepted(app, name):
    response = put_settings(app, protect(name))
    assert response.status_code == 200
    (host,) = response.json()["hosts"]
    assert name in host["services"]["compose:shop"]["protected_containers"]


@pytest.mark.parametrize("name", ["🚀", "---", "::"])
def test_a_protected_name_that_slugs_to_nothing_is_refused_with_the_uuid_hint(app, name):
    response = put_settings(app, protect(name))
    assert response.status_code == 422
    assert "protect the application by its uuid" in response.json()["detail"]
    (host,) = app.get("/api/settings").json()["hosts"]
    assert host["services"].get("compose:shop", {}).get("protected_containers", []) == []


def test_a_protected_entry_with_a_control_character_is_refused(app):
    response = put_settings(app, protect("shop\x1b[2Japi"))
    assert response.status_code == 422
    assert "control characters" in response.text
    assert "2Japi" not in response.text


def test_a_protected_entry_shaped_like_a_secret_is_refused(app):
    response = put_settings(app, protect("api_key=sk-live-0123456789abcdef"))
    assert response.status_code == 422
    assert "sk-live" not in response.text


def test_a_deeply_nested_body_is_refused_not_a_crash(app):
    body: dict = {"x": 1}
    for _ in range(1_000):
        body = {"hosts": body}
    assert put_settings(app, body).status_code == 422
    assert app.get("/api/settings").status_code == 200


def test_a_plugin_that_fails_to_build_is_a_422(app, monkeypatch):
    plugin = app.parts.plugins.get("coolify")

    def explode(ctx):
        raise RuntimeError("boom")

    monkeypatch.setattr(plugin, "tools", explode)
    response = put_plugin(
        app, "coolify", json={"enabled": True, "settings": {"url": "https://c.test"}}
    )
    assert response.status_code == 422
    assert app.get("/api/plugins").json()[0]["enabled"] is False


def test_a_read_carries_the_version_in_the_body_and_the_etag(app):
    response = app.get("/api/settings")
    assert response.headers["etag"] == f'"{response.json()["version"]}"'


def test_a_put_without_the_version_is_refused_and_nothing_changes(app):
    before = app.get("/api/settings").json()
    response = app.put("/api/settings", json={"thresholds": {"disk_percent": 80}})
    assert response.status_code == 428
    assert "If-Match" in response.json()["detail"]
    assert app.get("/api/settings").json() == before


def test_a_put_from_a_stale_page_is_a_409_with_the_current_version_and_nothing_changes(app):
    """A tab read before a container was protected elsewhere would unprotect it."""
    stale = app.get("/api/settings").json()["version"]
    protect = {"hosts": {"a": {"services": {"compose:shop": {"protected_containers": ["db-1"]}}}}}
    protected = put_settings(app, protect)
    assert protected.status_code == 200
    current = protected.json()["version"]
    assert current != stale
    assert protected.headers["etag"] == f'"{current}"'

    response = put_settings(app, {"hosts": {"a": {"rules": ["from the stale tab"]}}}, stale)
    assert response.status_code == 409
    assert response.json()["version"] == current
    assert "nothing was saved" in response.json()["detail"]
    body = app.get("/api/settings").json()
    assert body["version"] == current
    assert body["overrides"]["hosts"] == protect["hosts"]


def test_a_put_takes_the_version_bare_or_as_an_etag(app):
    version = app.get("/api/settings").json()["version"]
    bare = app.put(
        "/api/settings", json={"thresholds": {"disk_percent": 80}}, headers={"If-Match": version}
    )
    assert bare.status_code == 200
    weak = app.put(
        "/api/settings",
        json={"thresholds": {"disk_percent": 81}},
        headers={"If-Match": f'W/"{bare.json()["version"]}"'},
    )
    assert weak.status_code == 200
    assert (
        app.put("/api/settings", json={"thresholds": {}}, headers={"If-Match": "*"}).status_code
        == 409
    )


def test_of_two_saves_from_one_page_the_second_is_refused(app):
    """The dev panel's budgets and the settings page's thresholds, both read at once."""
    version = app.get("/api/settings").json()["version"]
    assert put_settings(app, {"budgets": {"daily_cost_usd": 0.5}}, version).status_code == 200
    second = put_settings(app, {"thresholds": {"disk_percent": 80}}, version)
    assert second.status_code == 409
    body = app.get("/api/settings").json()
    assert body["budgets"]["daily_cost_usd"] == 0.5
    assert body["thresholds"]["disk_percent"] == 90


def test_a_plugin_save_leaves_the_settings_version_alone(app):
    version = app.get("/api/settings").json()["version"]
    put_plugin(app, "coolify", json={"settings": {"url": "https://c.test"}})
    assert app.get("/api/settings").json()["version"] == version


def test_a_plugin_read_carries_its_version(app):
    (plugin,) = app.get("/api/plugins").json()
    assert isinstance(plugin["version"], str) and plugin["version"]


def test_a_plugin_put_without_the_version_is_refused_and_nothing_changes(app):
    response = app.put("/api/plugins/coolify", json={"settings": {"url": "https://c.test"}})
    assert response.status_code == 428
    assert "If-Match" in response.json()["detail"]
    assert app.get("/api/plugins").json()[0]["settings"] == {}


def test_a_plugin_put_from_a_stale_page_is_a_409_and_nothing_changes(app):
    """A tab read before a setting was changed elsewhere would put the old one back."""
    stale = app.get("/api/plugins").json()[0]["version"]
    saved = put_plugin(app, "coolify", json={"settings": {"url": "https://new.test"}})
    assert saved.status_code == 200
    current = saved.json()["version"]
    assert current != stale

    response = put_plugin(
        app, "coolify", json={"settings": {"url": "https://old.test"}}, version=stale
    )
    assert response.status_code == 409
    assert response.json()["version"] == current
    assert "nothing was saved" in response.json()["detail"]
    assert app.get("/api/plugins").json()[0]["settings"]["url"] == "https://new.test"


def test_a_settings_save_leaves_a_plugin_version_alone(app):
    version = app.get("/api/plugins").json()[0]["version"]
    put_settings(app, {"thresholds": {"disk_percent": 80}})
    assert app.get("/api/plugins").json()[0]["version"] == version
