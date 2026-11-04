"""Secrets and plugin addresses set from the dashboard over HTTP: write-only, the env wins, and a
plugin's address is bound to its token (#135)."""

import logging
import stat

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from bleep.hosts.registry import Registry
from bleep.main import create_app
from bleep.memory.embeddings import FakeEmbedder
from bleep.plugins.base import Plugin, PluginContext
from bleep.schemas.plugins import PluginHealth
from bleep.tools.registry import ToolSpec
from bleep.wiring import build_parts

TOKEN = "fk_dashboard-token-0123456789abcdef"
NEW_TOKEN = "fk_second-token-0123456789abcdef"
MODEL_KEY = "sk-or-v1-dashboard-key-0123456789"
BOT_TOKEN = "7001:AAH-dashboard_bot-token-value"
URL = "https://fake.example.test"
PUBLIC_IP = "93.184.216.34"


class FakeResolver:
    """Name lookups without the network: every name is public unless a test says otherwise."""

    def __init__(self) -> None:
        self.answers: dict[str, list[str]] = {}
        self.asked: list[str] = []

    async def __call__(self, host: str, port: int) -> list[str]:
        self.asked.append(host)
        return self.answers.get(host, [PUBLIC_IP])


class NoSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FakeUrlPlugin(Plugin):
    """A plugin with an address and a token, like Coolify: records each context it is built with."""

    name = "fake"
    title = "Fake"
    settings_model = NoSettings
    secrets = ("FAKE_URL", "FAKE_TOKEN")
    connection = ("FAKE_URL", "FAKE_TOKEN")

    def __init__(self) -> None:
        self.contexts: list[PluginContext] = []

    def tools(self, ctx: PluginContext) -> list[ToolSpec]:
        self.contexts.append(ctx)
        return []

    async def health(self, ctx: PluginContext) -> PluginHealth:
        return PluginHealth(ok=True, detail=f"reached as {ctx.secrets['FAKE_TOKEN']}")

    def sent_to(self) -> tuple[str, str]:
        ctx = self.contexts[-1]
        return ctx.secrets["FAKE_URL"].get_secret_value(), ctx.secrets[
            "FAKE_TOKEN"
        ].get_secret_value()


@pytest.fixture
def plugin() -> FakeUrlPlugin:
    return FakeUrlPlugin()


@pytest.fixture
def resolver() -> FakeResolver:
    return FakeResolver()


@pytest.fixture
def app(settings, plugin, resolver, monkeypatch):
    for name in ("FAKE_URL", "FAKE_TOKEN", "OPENROUTER_API_KEY", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    parts = build_parts(settings, Registry([]), FakeEmbedder(), plugins=[plugin], resolver=resolver)
    parts.notify.start = lambda runner: None  # type: ignore[method-assign]  # never polls
    with TestClient(create_app(settings, parts)) as client:
        assert client.post("/api/auth/login", json={"password": "test-password"}).status_code == 204
        client.parts = parts  # type: ignore[attr-defined]
        yield client


def put(client, name: str, value):
    return client.put(f"/api/secrets/{name}", json={"value": value})


def put_token(client, value: str, name: str = "FAKE_TOKEN", address: str = "FAKE_URL"):
    """The token typed for the address as the page last read it."""
    version = listed(client)[address]["version"]
    return client.put(f"/api/secrets/{name}", json={"value": value, "address_version": version})


def listed(client) -> dict[str, dict]:
    return {view["name"]: view for view in client.get("/api/secrets").json()}


def switch_on(client, name: str = "fake"):
    (view,) = [p for p in client.get("/api/plugins").json() if p["name"] == name]
    return client.put(
        f"/api/plugins/{name}", json={"enabled": True}, headers={"If-Match": view["version"]}
    )


def plugin_view(client) -> dict:
    (view,) = [p for p in client.get("/api/plugins").json() if p["name"] == "fake"]
    return view


def test_the_list_names_every_secret_and_never_the_login_or_the_session_secret(app):
    views = listed(app)
    assert {"OPENROUTER_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GITHUB_TOKEN"} <= set(
        views
    )
    assert {"EMBEDDINGS_API_KEY", "LANGFUSE_SECRET_KEY", "LANGSMITH_API_KEY"} <= set(views)
    assert views["FAKE_URL"]["kind"] == "address" and views["FAKE_URL"]["carries"] == "FAKE_TOKEN"
    assert not {"BLEEP_ADMIN_PASSWORD", "BLEEP_SECRET"} & set(views)
    assert views["GITHUB_TOKEN"] == {
        "name": "GITHUB_TOKEN",
        "kind": "secret",
        "set": False,
        "source": None,
        "env_owned": False,
        "carries": None,
        "value": None,
        "version": None,
        "invalid": False,
    }


def test_a_secret_set_is_shown_only_as_set_and_lands_in_a_private_file_not_the_database(
    app, settings, caplog
):
    caplog.set_level(logging.DEBUG)
    response = put(app, "OPENROUTER_API_KEY", MODEL_KEY)

    assert response.status_code == 200
    view = {v["name"]: v for v in response.json()}["OPENROUTER_API_KEY"]
    assert view["set"] and view["source"] == "dashboard" and view["value"] is None
    file = settings.data_dir / "secrets.json"
    assert stat.S_IMODE(file.stat().st_mode) == 0o600
    assert MODEL_KEY in file.read_text()
    for page in ("/api/secrets", "/api/settings", "/api/plugins", "/api/telegram/status"):
        assert MODEL_KEY not in app.get(page).text
    assert MODEL_KEY.encode() not in (settings.data_dir / "bleep.db").read_bytes()
    assert MODEL_KEY not in caplog.text


def test_the_model_key_set_on_the_dashboard_is_used_at_the_next_model_call(app):
    assert app.parts.settings_store.models().openrouter_api_key is None
    put(app, "OPENROUTER_API_KEY", MODEL_KEY)

    key = app.parts.settings_store.models().openrouter_api_key
    assert key is not None and key.get_secret_value() == MODEL_KEY
    assert app.get("/api/settings").json()["model_key"]["source"] == "dashboard"
    assert app.delete("/api/secrets/OPENROUTER_API_KEY").status_code == 200
    assert app.parts.settings_store.models().openrouter_api_key is None


def test_the_github_token_set_on_the_dashboard_is_used_at_the_next_request(app):
    assert not app.parts.github.authenticated
    put(app, "GITHUB_TOKEN", "ghp_dashboard0123456789")
    assert app.parts.github.authenticated


def test_a_value_from_the_env_wins_and_cannot_be_set_or_cleared_here(settings, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-from-env")
    parts = build_parts(settings, Registry([]), FakeEmbedder())
    with TestClient(create_app(settings, parts)) as client:
        client.post("/api/auth/login", json={"password": "test-password"})
        view = listed(client)["OPENROUTER_API_KEY"]
        assert view["env_owned"] and view["source"] == "env" and view["set"]

        set_response = put(client, "OPENROUTER_API_KEY", MODEL_KEY)
        clear_response = client.delete("/api/secrets/OPENROUTER_API_KEY")
    assert set_response.status_code == clear_response.status_code == 409
    assert "environment" in set_response.json()["detail"]
    assert MODEL_KEY not in set_response.text and "from-env" not in set_response.text


@pytest.mark.parametrize("name", ["BLEEP_SECRET", "BLEEP_ADMIN_PASSWORD", "PATH"])
def test_a_name_not_on_the_list_is_a_404(app, name):
    assert put(app, name, "anything-at-all").status_code == 404
    assert app.delete(f"/api/secrets/{name}").status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"value": 12345},
        {"value": MODEL_KEY, "extra": MODEL_KEY},
        {"secret": MODEL_KEY},
        [MODEL_KEY],
        {"value": f"{MODEL_KEY}\n{MODEL_KEY}"},
    ],
)
def test_a_body_that_does_not_fit_is_refused_without_echoing_it(app, body):
    response = app.put("/api/secrets/OPENROUTER_API_KEY", json=body)
    assert response.status_code == 422
    assert MODEL_KEY not in response.text
    assert not listed(app)["OPENROUTER_API_KEY"]["set"]


def test_a_body_over_the_cap_is_refused_before_it_is_read(app):
    response = app.put(
        "/api/secrets/OPENROUTER_API_KEY",
        content=b'{"value": "' + b"a" * 500_000 + b'"}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_the_api_needs_a_session(settings):
    parts = build_parts(settings, Registry([]), FakeEmbedder())
    with TestClient(create_app(settings, parts)) as client:
        assert client.get("/api/secrets").status_code == 401
        assert put(client, "OPENROUTER_API_KEY", MODEL_KEY).status_code == 401


@pytest.mark.parametrize(
    "url",
    ["http://203.0.113.9:8000", "https://user:pw@fake.example.test", "ftp://fake", "not a url"],
)
def test_an_address_is_held_to_the_url_rule_and_never_quoted(app, url):
    response = put(app, "FAKE_URL", url)
    assert response.status_code == 422
    assert url not in response.text and "FAKE_URL" in response.json()["detail"]


@pytest.mark.parametrize("url", ["http://192.168.1.20:8000", "http://shop-coolify:8000"])
def test_plain_http_is_for_env_only_even_to_a_private_address(app, url):
    response = put(app, "FAKE_URL", url)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "must be https" in detail and ".env" in detail and url not in response.text


@pytest.mark.parametrize(
    "addresses",
    [
        ["10.0.0.5"],
        ["127.0.0.1"],
        ["169.254.169.254"],
        ["100.64.0.7"],
        ["::1"],
        ["fd00::5"],
        ["::ffff:10.0.0.1"],
        ["fe80::1%eth0"],
        ["0.0.0.0"],
        ["224.0.0.1"],
        ["64:ff9b::a00:1"],
        [PUBLIC_IP, "10.0.0.5"],  # one private answer among public ones is enough
        [],
    ],
)
def test_a_dashboard_address_must_resolve_to_public_addresses_only(app, resolver, addresses):
    resolver.answers["fake.example.test"] = addresses
    response = put(app, "FAKE_URL", URL)
    assert response.status_code == 422
    assert ".env" in response.json()["detail"]
    assert not any(a and a in response.text for a in addresses)
    assert not listed(app)["FAKE_URL"]["set"]


@pytest.mark.parametrize("literal", ["https://10.0.0.5", "https://[::1]:8443", "https://127.0.0.1"])
def test_a_private_address_literal_is_refused_too(app, literal):
    assert put(app, "FAKE_URL", literal).status_code == 422


def test_an_address_shows_only_its_origin_never_its_path(app):
    secret_path = "https://fake.example.test:8443/api/mcp/s/sk-path-secret-0123456789/mcp"
    assert put(app, "FAKE_URL", secret_path).status_code == 200

    response = app.get("/api/secrets")
    view = listed(app)["FAKE_URL"]
    assert view["value"] == "https://fake.example.test:8443" and view["version"]
    assert "sk-path-secret" not in response.text


def test_an_address_from_the_env_is_not_shown_at_all(settings, monkeypatch):
    secret_path = "http://10.0.0.5:8000/api/mcp/s/sk-path-secret-0123456789/mcp"
    monkeypatch.setenv("FAKE_URL", secret_path)
    parts = build_parts(settings, Registry([]), FakeEmbedder(), plugins=[FakeUrlPlugin()])
    with TestClient(create_app(settings, parts)) as client:
        client.post("/api/auth/login", json={"password": "test-password"})
        response = client.get("/api/secrets")
    view = {v["name"]: v for v in response.json()}["FAKE_URL"]
    assert view["set"] and view["source"] == "env"
    assert view["value"] is None and view["version"] is None
    assert "sk-path-secret" not in response.text and "10.0.0.5" not in response.text


def test_a_token_typed_for_an_address_changed_since_is_not_stored(app, plugin):
    """Another tab changed the address after this one read it: the token is refused."""
    put(app, "FAKE_URL", URL)
    seen = listed(app)["FAKE_URL"]["version"]
    put(app, "FAKE_URL", "https://elsewhere.example.test")

    stale = app.put("/api/secrets/FAKE_TOKEN", json={"value": TOKEN, "address_version": seen})
    missing = app.put("/api/secrets/FAKE_TOKEN", json={"value": TOKEN})

    assert stale.status_code == 409 and "read it again" in stale.json()["detail"]
    assert missing.status_code == 428
    assert TOKEN not in stale.text + missing.text
    assert not listed(app)["FAKE_TOKEN"]["set"]


@pytest.mark.parametrize(
    "spelling",
    ["https://FAKE.example.test", "https://fake.example.test/", "https://fake.example.test:443"],
)
def test_the_same_address_spelled_differently_keeps_the_token(app, spelling):
    put(app, "FAKE_URL", URL)
    put_token(app, TOKEN)
    assert put(app, "FAKE_URL", spelling).status_code == 200
    assert listed(app)["FAKE_TOKEN"]["set"]


def test_a_token_needs_its_address_first(app):
    response = put_token(app, TOKEN)
    assert response.status_code == 422
    assert "FAKE_URL first" in response.json()["detail"] and TOKEN not in response.text


def test_a_plugin_runs_with_the_address_and_token_set_on_the_dashboard(app, plugin, resolver):
    put(app, "FAKE_URL", URL)
    put_token(app, TOKEN)
    response = switch_on(app)

    assert response.status_code == 200 and response.json()["enabled"]
    assert plugin.sent_to() == (URL, TOKEN)
    assert plugin.contexts[-1].public_only is resolver  # it must send through the guard
    assert TOKEN not in app.get("/api/plugins").text  # the health check echoes it


def test_changing_a_plugins_address_clears_its_token_and_switches_it_off(app, plugin):
    put(app, "FAKE_URL", URL)
    put_token(app, TOKEN)
    switch_on(app)

    response = put(app, "FAKE_URL", "https://elsewhere.example.test")

    views = {v["name"]: v for v in response.json()}
    assert not views["FAKE_TOKEN"]["set"]
    assert TOKEN not in (app.parts.secrets.path.read_text())
    view = plugin_view(app)
    assert not view["enabled"] and not view["switched_on"]
    assert not app.parts.plugins.is_enabled("fake")
    # Switching it on again is refused until a token is entered for the new address.
    refused = switch_on(app)
    assert refused.status_code == 422 and "FAKE_TOKEN" in refused.json()["detail"]
    put_token(app, NEW_TOKEN)
    assert switch_on(app).status_code == 200
    assert plugin.sent_to() == ("https://elsewhere.example.test", NEW_TOKEN)


def test_clearing_a_plugins_address_clears_its_token_and_switches_it_off(app, plugin):
    put(app, "FAKE_URL", URL)
    put_token(app, TOKEN)
    switch_on(app)

    app.delete("/api/secrets/FAKE_URL")

    assert not listed(app)["FAKE_TOKEN"]["set"]
    assert not plugin_view(app)["switched_on"]


def test_a_plugin_switched_off_by_an_address_change_stays_off_after_a_restart(
    app, settings, plugin
):
    put(app, "FAKE_URL", URL)
    put_token(app, TOKEN)
    switch_on(app)
    put(app, "FAKE_URL", "https://elsewhere.example.test")
    put_token(app, NEW_TOKEN)

    again = FakeUrlPlugin()
    parts = build_parts(settings, Registry([]), FakeEmbedder(), plugins=[again])
    with TestClient(create_app(settings, parts)):
        assert not parts.plugins.is_enabled("fake")


def test_a_new_token_for_the_same_address_is_used_by_the_running_plugin(app, plugin):
    put(app, "FAKE_URL", URL)
    put_token(app, TOKEN)
    switch_on(app)

    put_token(app, NEW_TOKEN)

    assert app.parts.plugins.is_enabled("fake")
    assert plugin.sent_to() == (URL, NEW_TOKEN)


def test_a_cleared_token_stops_the_plugin_which_waits_for_it(app, plugin):
    put(app, "FAKE_URL", URL)
    put_token(app, TOKEN)
    switch_on(app)

    app.delete("/api/secrets/FAKE_TOKEN")

    view = plugin_view(app)
    assert not view["enabled"] and view["switched_on"]  # "On, waiting for FAKE_TOKEN"
    put_token(app, NEW_TOKEN)
    assert plugin_view(app)["enabled"] and plugin.sent_to() == (URL, NEW_TOKEN)


def test_an_env_token_is_never_sent_to_an_address_set_on_the_dashboard(
    settings, plugin, monkeypatch
):
    """The env sets the token only: the address cannot be set here, so the token goes nowhere
    the dashboard chose."""
    monkeypatch.setenv("FAKE_TOKEN", TOKEN)
    monkeypatch.delenv("FAKE_URL", raising=False)
    parts = build_parts(
        settings, Registry([]), FakeEmbedder(), plugins=[plugin], resolver=FakeResolver()
    )
    with TestClient(create_app(settings, parts)) as client:
        client.post("/api/auth/login", json={"password": "test-password"})
        response = put(client, "FAKE_URL", URL)
        assert response.status_code == 409
        assert "FAKE_TOKEN and FAKE_URL come from the environment" in response.json()["detail"]
        refused = switch_on(client)
    assert refused.status_code == 422 and "FAKE_URL" in refused.json()["detail"]
    assert plugin.contexts == []


def test_an_env_address_takes_the_dashboard_token_out_of_use(settings, plugin, monkeypatch):
    """Set on the dashboard first, then the env names an address: the pair comes from the env,
    so the dashboard's token is not sent to the env's address either."""
    monkeypatch.delenv("FAKE_URL", raising=False)
    monkeypatch.delenv("FAKE_TOKEN", raising=False)
    parts = build_parts(
        settings, Registry([]), FakeEmbedder(), plugins=[plugin], resolver=FakeResolver()
    )
    with TestClient(create_app(settings, parts)) as client:
        client.post("/api/auth/login", json={"password": "test-password"})
        assert put(client, "FAKE_URL", URL).status_code == 200
        assert put_token(client, TOKEN).status_code == 200
    assert parts.secrets.get("FAKE_TOKEN") is not None

    monkeypatch.setenv("FAKE_URL", "https://env.example.test")
    parts = build_parts(settings, Registry([]), FakeEmbedder(), plugins=[FakeUrlPlugin()])
    assert parts.secrets.get("FAKE_TOKEN") is None
    assert parts.secrets.environ().get("FAKE_TOKEN") is None


def test_telegram_set_on_the_dashboard_starts_the_bot_without_a_restart(app):
    assert not app.get("/api/telegram/status").json()["configured"]

    put(app, "TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    put(app, "TELEGRAM_CHAT_ID", "-1001234567890")

    status = app.get("/api/telegram/status")
    assert status.json()["configured"] is True
    assert status.json()["token"]["source"] == "dashboard"
    assert BOT_TOKEN not in status.text and "1001234567890" not in app.get("/api/secrets").text
    assert listed(app)["TELEGRAM_CHAT_ID"]["value"] == "••••90"
    notifier = app.parts.graph_deps.notifier
    assert notifier is app.parts.notify.notifier and notifier.telegram is not None
    assert notifier.chat_id == "-1001234567890"

    app.delete("/api/secrets/TELEGRAM_BOT_TOKEN")
    assert not app.get("/api/telegram/status").json()["configured"]
    assert notifier.telegram is None


def test_a_plugin_is_stored_off_before_its_new_address_is_written(app, plugin, monkeypatch):
    """The process dies or the write fails after the plugin row and before the file: the plugin
    is already stored off, so it never starts at the new address by itself."""
    import os

    put(app, "FAKE_URL", URL)
    put_token(app, TOKEN)
    switch_on(app)

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    response = put(app, "FAKE_URL", "https://elsewhere.example.test")
    monkeypatch.undo()

    assert response.status_code == 500 and "elsewhere" not in response.text
    assert app.parts.secrets.get("FAKE_URL").get_secret_value() == URL  # the file is as it was
    view = plugin_view(app)
    assert not view["enabled"] and not view["switched_on"]


def test_a_failure_to_store_the_plugin_off_saves_nothing(app, plugin, monkeypatch):
    put(app, "FAKE_URL", URL)
    put_token(app, TOKEN)
    switch_on(app)

    async def fail(sections):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(app.parts.settings_store, "_write", fail)
    response = put(app, "FAKE_URL", "https://elsewhere.example.test")
    monkeypatch.undo()

    assert response.status_code == 500
    assert app.parts.secrets.get("FAKE_URL").get_secret_value() == URL
    assert app.parts.secrets.get("FAKE_TOKEN") is not None
    assert plugin_view(app)["enabled"] and plugin.sent_to() == (URL, TOKEN)


@pytest.mark.parametrize(
    "stored",
    [
        "https://[::1/sk-hand-edited-secret",  # does not parse
        "http://socket-proxy:2375/sk-hand-edited-secret",  # parses, but not https
        "not a url sk-hand-edited-secret",
    ],
)
def test_a_hand_edited_address_is_shown_as_invalid_never_quoted(settings, stored, monkeypatch):
    import json

    monkeypatch.delenv("FAKE_URL", raising=False)
    monkeypatch.delenv("FAKE_TOKEN", raising=False)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "secrets.json").write_text(
        json.dumps({"version": 1, "values": {"FAKE_URL": stored}})
    )
    parts = build_parts(
        settings, Registry([]), FakeEmbedder(), plugins=[FakeUrlPlugin()], resolver=FakeResolver()
    )
    with TestClient(create_app(settings, parts)) as client:
        client.post("/api/auth/login", json={"password": "test-password"})
        response = client.get("/api/secrets")
        switched = switch_on(client)
    assert response.status_code == 200
    view = {v["name"]: v for v in response.json()}["FAKE_URL"]
    assert view["set"] and view["invalid"] and view["value"] is None
    assert "sk-hand-edited" not in response.text and "socket-proxy" not in response.text
    assert switched.status_code == 422 and "sk-hand-edited" not in switched.text
