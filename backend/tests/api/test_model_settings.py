"""The model, embeddings and tracing settings from the dashboard (#372): the env wins and the view
says so, each change applies at once or the view says a restart is needed, and an address set
here stays bound to the keys sent to it."""

import logging
import uuid

import pytest
from fastapi.testclient import TestClient

from bleep.config import Settings, SettingsError
from bleep.hosts.registry import Registry
from bleep.llm.tracing import LangfuseTracing, LangSmithTracing, NoTracing, ReloadingTracer
from bleep.main import create_app
from bleep.memory.embeddings import FakeEmbedder
from bleep.schemas.settings import ModelSettings
from bleep.wiring import build_parts
from fakes import database_bytes

ENV_NAMES = (
    "LLM_PROVIDER",
    "LLM_MODEL",
    "LLM_CHEAP_MODEL",
    "LOCAL_BASE_URL",
    "LOCAL_MODEL",
    "OPENROUTER_API_KEY",
    "EMBEDDINGS_PROVIDER",
    "EMBEDDINGS_BASE_URL",
    "EMBEDDINGS_MODEL",
    "EMBEDDINGS_MIN_SCORE",
    "EMBEDDINGS_API_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_BASE_URL",
    "LANGFUSE_UI_URL",
    "LANGFUSE_PROJECT_ID",
    "LANGSMITH_API_KEY",
    "LANGSMITH_ENDPOINT",
    "LANGSMITH_PROJECT",
)
LANGSMITH_KEY = "lsv2_pt_dashboard-key-0123456789abcdef"
EMBEDDINGS_KEY = "sk-embeddings-dashboard-key-0123456789"
PUBLIC_IP = "93.184.216.34"


class FakeResolver:
    def __init__(self) -> None:
        self.answers: dict[str, list[str]] = {}

    async def __call__(self, host: str, port: int) -> list[str]:
        return self.answers.get(host, [PUBLIC_IP])


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def resolver() -> FakeResolver:
    return FakeResolver()


@pytest.fixture
def start(settings, resolver):
    """Start Bleep over the test's data directory; call again for a restart."""
    clients: list[TestClient] = []

    def started(given: Settings | None = None) -> TestClient:
        given = given or settings
        parts = build_parts(given, Registry([]), FakeEmbedder(), plugins=[], resolver=resolver)
        parts.notify.start = lambda runner: None  # type: ignore[method-assign]
        client = TestClient(create_app(given, parts))
        client.__enter__()
        clients.append(client)
        assert client.post("/api/auth/login", json={"password": "test-password"}).status_code == 204
        client.parts = parts  # type: ignore[attr-defined]
        return client

    yield started
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def app(start):
    return start()


def put_settings(client: TestClient, body: object):
    version = client.get("/api/settings").json()["version"]
    return client.put("/api/settings", json=body, headers={"If-Match": f'"{version}"'})


def put_secret(client: TestClient, name: str, value: str, address: str | None = None):
    body: dict[str, str] = {"value": value}
    if address is not None:
        listed = {v["name"]: v for v in client.get("/api/secrets").json()}
        body["address_version"] = listed[address]["version"]
    return client.put(f"/api/secrets/{name}", json=body)


def secrets(client: TestClient) -> dict[str, dict]:
    return {v["name"]: v for v in client.get("/api/secrets").json()}


def inner_tracer(client: TestClient):
    tracer = client.parts.tracer
    assert isinstance(tracer, ReloadingTracer)
    return tracer.current()


def test_the_view_shows_embeddings_and_tracing_in_force_and_nothing_from_the_env(app):
    body = app.get("/api/settings").json()
    assert body["embeddings"] == {"provider": "local", "model": None, "min_score": None}
    assert body["tracing"]["tracing"] == "off"
    assert body["tracing"]["langsmith_project"] == "bleep"
    assert body["from_env"] == [] and body["restart_needed"] == []


def test_a_model_chosen_on_the_page_is_used_at_the_next_model_call(app):
    response = put_settings(app, {"models": {"provider": "local", "local_model": "qwen3:8b"}})
    assert response.status_code == 200
    models = app.parts.settings_store.models()
    assert (models.provider, models.local_model) == ("local", "qwen3:8b")


def test_a_model_the_env_sets_wins_and_the_page_cannot_change_it(start, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "local")
    monkeypatch.setenv("LLM_MODEL", "openai/env-model")
    app = start()
    body = app.get("/api/settings").json()
    assert {"models.provider", "models.model"} <= set(body["from_env"])

    refused = put_settings(app, {"models": {"model": "openai/other"}})
    assert refused.status_code == 422
    assert "LLM_MODEL is set in the environment" in refused.json()["detail"]
    assert put_settings(app, {"models": {"cheap_model": "openai/cheap"}}).status_code == 200
    models = app.parts.settings_store.models()
    assert (models.model, models.cheap_model) == ("openai/env-model", "openai/cheap")


def test_an_override_stored_before_the_env_set_the_field_is_not_in_force(start, monkeypatch):
    app = start()
    assert put_settings(app, {"models": {"model": "openai/dashboard"}}).status_code == 200
    monkeypatch.setenv("LLM_MODEL", "openai/env-model")
    again = start()
    assert again.parts.settings_store.models().model == "openai/env-model"
    # The stale override can be sent again as it is with another change, or left out to clear.
    response = put_settings(
        again, {"models": {"model": "openai/dashboard", "cheap_model": "openai/c"}}
    )
    assert response.status_code == 200


@pytest.mark.parametrize(
    "url",
    [
        "http://host.docker.internal:1234/v1",
        "http://192.168.1.20:11434/v1",
        "http://10.0.0.5:8080/v1",
        "http://ollama:11434/v1",
        "https://models.example.test/v1",
    ],
)
def test_a_local_model_address_may_be_private_and_is_used_at_the_next_call(app, url):
    response = put_secret(app, "LOCAL_BASE_URL", url)
    assert response.status_code == 200
    assert app.parts.settings_store.models().local_base_url == url.rstrip("/")


@pytest.mark.parametrize("url", ["http://models.example.test/v1", "ftp://x/v1", "http://u:p@x/v1"])
def test_a_model_address_over_plain_http_to_a_public_name_is_refused(app, url):
    response = put_secret(app, "LOCAL_BASE_URL", url)
    assert response.status_code == 422
    assert url not in response.text


def test_the_embeddings_address_is_bound_to_its_key(app):
    listed = secrets(app)
    assert listed["EMBEDDINGS_BASE_URL"]["private"] and listed["LOCAL_BASE_URL"]["private"]
    assert listed["EMBEDDINGS_BASE_URL"]["carries"] == "EMBEDDINGS_API_KEY"
    assert not listed["GITHUB_TOKEN"]["private"]
    first = put_secret(app, "EMBEDDINGS_API_KEY", EMBEDDINGS_KEY, address="EMBEDDINGS_BASE_URL")
    assert first.status_code == 422 and "EMBEDDINGS_BASE_URL first" in first.json()["detail"]

    assert put_secret(app, "EMBEDDINGS_BASE_URL", "http://host.docker.internal:1234/v1").is_success
    assert put_secret(
        app, "EMBEDDINGS_API_KEY", EMBEDDINGS_KEY, address="EMBEDDINGS_BASE_URL"
    ).is_success
    assert secrets(app)["EMBEDDINGS_API_KEY"]["set"]

    assert put_secret(app, "EMBEDDINGS_BASE_URL", "http://192.168.1.20:11434/v1").is_success
    assert not secrets(app)["EMBEDDINGS_API_KEY"]["set"]  # a new address clears its key


def test_an_embeddings_endpoint_needs_its_address_and_model_first(app):
    no_address = put_settings(app, {"embeddings": {"provider": "openai", "model": "bge-m3"}})
    assert no_address.status_code == 422
    assert "EMBEDDINGS_BASE_URL first" in no_address.json()["detail"]

    put_secret(app, "EMBEDDINGS_BASE_URL", "http://host.docker.internal:1234/v1")
    no_model = put_settings(app, {"embeddings": {"provider": "openai"}})
    assert no_model.status_code == 422 and "EMBEDDINGS_MODEL" in no_model.json()["detail"]

    saved = put_settings(app, {"embeddings": {"provider": "openai", "model": "bge-m3"}})
    assert saved.status_code == 200
    assert saved.json()["embeddings"] == {
        "provider": "openai",
        "model": "bge-m3",
        "min_score": None,
    }


def test_a_local_server_needs_its_address_and_model_but_no_key(app):
    no_address = put_settings(app, {"embeddings": {"provider": "server", "model": "bge-m3"}})
    assert no_address.status_code == 422
    assert "EMBEDDINGS_BASE_URL first" in no_address.json()["detail"]

    put_secret(app, "EMBEDDINGS_BASE_URL", "http://host.docker.internal:1234/v1")
    no_model = put_settings(app, {"embeddings": {"provider": "server"}})
    assert no_model.status_code == 422 and "EMBEDDINGS_MODEL" in no_model.json()["detail"]

    app.parts.settings_store.start_embeddings()
    saved = put_settings(app, {"embeddings": {"provider": "server", "model": "bge-m3"}})
    assert saved.status_code == 200  # no EMBEDDINGS_API_KEY was needed
    assert saved.json()["embeddings"]["provider"] == "server"
    assert saved.json()["restart_needed"] == ["embeddings"]  # it re-embeds at the next start


def test_the_embeddings_address_is_not_cleared_while_a_local_server_embeds(app):
    put_secret(app, "EMBEDDINGS_BASE_URL", "http://host.docker.internal:1234/v1")
    put_settings(app, {"embeddings": {"provider": "server", "model": "bge-m3"}})
    app.parts.settings_store.start_embeddings()

    refused = app.delete("/api/secrets/EMBEDDINGS_BASE_URL")
    assert refused.status_code == 409 and "built-in model" in refused.json()["detail"]


def test_an_embeddings_provider_the_env_sets_wins_over_a_local_server_on_the_page(
    start, settings, monkeypatch
):
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "local")
    app = start(
        Settings(
            **settings.model_dump(include={"data_dir", "hosts_file", "admin_password", "secret"})
        )
    )
    put_secret(app, "EMBEDDINGS_BASE_URL", "http://host.docker.internal:1234/v1")
    refused = put_settings(app, {"embeddings": {"provider": "server", "model": "bge-m3"}})
    assert refused.status_code == 422 and "EMBEDDINGS_PROVIDER" in refused.json()["detail"]


def test_a_changed_embedder_says_a_restart_is_needed(app):
    store = app.parts.settings_store
    store.start_embeddings()  # what the running embedder was built with
    response = put_settings(app, {"embeddings": {"min_score": 0.4}})
    assert response.json()["restart_needed"] == ["embeddings"]
    assert put_settings(app, {"embeddings": {}}).json()["restart_needed"] == []


def test_an_embeddings_field_the_env_sets_wins(start, settings, monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_MIN_SCORE", "0.3")
    app = start(
        Settings(
            **settings.model_dump(include={"data_dir", "hosts_file", "admin_password", "secret"})
        )
    )
    body = app.get("/api/settings").json()
    assert body["from_env"] == ["embeddings.min_score"]
    assert body["embeddings"]["min_score"] == 0.3
    refused = put_settings(app, {"embeddings": {"min_score": 0.4}})
    assert refused.status_code == 422 and "EMBEDDINGS_MIN_SCORE" in refused.json()["detail"]


def test_tracing_is_switched_on_from_the_page_at_once_and_needs_its_key_first(app, settings):
    missing = put_settings(app, {"tracing": {"tracing": "langsmith"}})
    assert missing.status_code == 422 and "LANGSMITH_API_KEY" in missing.json()["detail"]
    assert isinstance(inner_tracer(app), NoTracing)

    assert put_secret(app, "LANGSMITH_API_KEY", LANGSMITH_KEY).is_success
    project = {"tracing": "langsmith", "langsmith_project": "ops-tracing-project"}
    response = put_settings(app, {"tracing": project})
    assert response.status_code == 200
    tracer = inner_tracer(app)
    assert isinstance(tracer, LangSmithTracing) and tracer._project == "ops-tracing-project"
    stored = database_bytes(settings.data_dir)
    assert b"ops-tracing-project" in stored  # the check reads what was written
    assert LANGSMITH_KEY.encode() not in stored
    assert LANGSMITH_KEY not in app.get("/api/settings").text

    assert put_settings(app, {"tracing": {}}).status_code == 200
    assert isinstance(inner_tracer(app), NoTracing)


def test_tracing_chosen_on_the_page_is_in_force_after_a_restart(start):
    app = start()
    put_secret(app, "LANGSMITH_API_KEY", LANGSMITH_KEY)
    put_settings(app, {"tracing": {"tracing": "langsmith"}})
    assert isinstance(inner_tracer(start()), LangSmithTracing)


def test_tracing_from_the_page_whose_key_is_gone_starts_bleep_with_tracing_off(
    start, settings, caplog
):
    app = start()
    put_secret(app, "LANGSMITH_API_KEY", LANGSMITH_KEY)
    put_settings(app, {"tracing": {"tracing": "langsmith"}})
    app.delete("/api/secrets/LANGSMITH_API_KEY")
    assert isinstance(inner_tracer(app), NoTracing)
    caplog.set_level(logging.WARNING)
    assert isinstance(inner_tracer(start()), NoTracing)
    assert "tracing is off" in caplog.text


def test_tracing_the_env_sets_wins(start, monkeypatch):
    monkeypatch.setenv("TRACING", "off")
    app = start()
    assert "tracing.tracing" in app.get("/api/settings").json()["from_env"]
    refused = put_settings(app, {"tracing": {"tracing": "langsmith"}})
    assert (
        refused.status_code == 422
        and "TRACING is set in the environment" in refused.json()["detail"]
    )


def test_a_tracer_address_is_https_to_public_addresses_only(app, resolver):
    plain = put_settings(app, {"tracing": {"langsmith_endpoint": "http://eu.example.test"}})
    assert plain.status_code == 422 and "https" in plain.json()["detail"]
    resolver.answers["smith.internal.test"] = ["10.0.0.7"]
    private = put_settings(app, {"tracing": {"langsmith_endpoint": "https://smith.internal.test"}})
    assert private.status_code == 422 and "public" in private.json()["detail"]
    assert "smith.internal.test" not in private.text

    public = put_settings(app, {"tracing": {"langsmith_endpoint": "https://eu.example.test/"}})
    assert public.status_code == 200
    assert public.json()["tracing"]["langsmith_endpoint"] == "https://eu.example.test"


def test_a_tracer_address_changes_only_while_its_key_is_unset(app):
    put_secret(app, "LANGSMITH_API_KEY", LANGSMITH_KEY)
    refused = put_settings(app, {"tracing": {"langsmith_endpoint": "https://eu.example.test"}})
    assert refused.status_code == 422
    assert "clear LANGSMITH_API_KEY first" in refused.json()["detail"]
    # The project is not an address: it changes with the key set.
    assert put_settings(app, {"tracing": {"langsmith_project": "ops"}}).status_code == 200


def test_a_key_from_the_env_keeps_its_tracer_address_in_the_env(start, monkeypatch):
    app = start()
    put_settings(app, {"tracing": {"langsmith_endpoint": "https://eu.example.test"}})
    monkeypatch.setenv("LANGSMITH_API_KEY", LANGSMITH_KEY)
    again = start()
    body = again.get("/api/settings").json()
    assert "tracing.langsmith_endpoint" in body["from_env"]
    assert body["tracing"]["langsmith_endpoint"] is None  # the env's: the default, not the page's
    refused = put_settings(again, {"tracing": {"langsmith_endpoint": "https://us.example.test"}})
    assert refused.status_code == 422
    assert "LANGSMITH_API_KEY is set in the environment" in refused.json()["detail"]


def test_a_tracer_address_with_a_password_is_refused_without_echoing_it(app):
    url = "https://user:hunter2-secret@eu.example.test"
    response = put_settings(app, {"tracing": {"langsmith_endpoint": url}})
    assert response.status_code == 422
    assert "hunter2" not in response.text


def test_langfuse_from_the_env_sends_to_the_address_set_on_the_page_after_a_restart(
    start, monkeypatch
):
    """The tracer is built only after the stored settings load: Langfuse keeps one client per
    public key, so one built before with the default address would be the one used after."""
    public = f"pk-lf-{uuid.uuid4().hex}"
    app = start()
    put_settings(app, {"tracing": {"langfuse_base_url": "https://cloud.example.test"}})
    assert put_secret(app, "LANGFUSE_PUBLIC_KEY", public).is_success
    assert put_secret(app, "LANGFUSE_SECRET_KEY", f"sk-lf-{uuid.uuid4().hex}").is_success

    monkeypatch.setenv("TRACING", "langfuse")
    again = start()
    tracer = inner_tracer(again)
    assert isinstance(tracer, LangfuseTracing)
    assert tracer._client._resources.base_url == "https://cloud.example.test"
    assert again.get("/api/settings").json()["restart_needed"] == []


def test_a_change_to_a_running_langfuse_tracer_waits_for_a_restart(start, monkeypatch):
    public = f"pk-lf-{uuid.uuid4().hex}"
    monkeypatch.setenv("TRACING", "langfuse")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", public)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", f"sk-lf-{uuid.uuid4().hex}")
    app = start()
    running = inner_tracer(app)
    assert isinstance(running, LangfuseTracing)

    assert put_settings(app, {"tracing": {"langfuse_ui_url": "https://lf.example.test"}}).is_success
    assert inner_tracer(app) is running  # never a second client for the same public key
    assert app.get("/api/settings").json()["restart_needed"] == ["tracing"]
    assert put_settings(app, {"tracing": {}}).json()["restart_needed"] == []


def test_a_tracer_address_with_a_password_in_the_env_stops_bleep_without_quoting_it(
    start, monkeypatch
):
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://probeuser:probe-password-123@lf.example.test")
    with pytest.raises(SettingsError) as refused:
        start()
    assert "LANGFUSE_BASE_URL" in str(refused.value)
    assert "probe-password-123" not in str(refused.value)


def test_a_tracer_address_from_the_env_is_shown_as_its_origin_only(start, monkeypatch):
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://eu.example.test/tenant-a1b2/api")
    app = start()
    response = app.get("/api/settings")
    assert response.json()["tracing"]["langsmith_endpoint"] == "https://eu.example.test"
    assert "tenant-a1b2" not in response.text


@pytest.mark.parametrize(
    "project",
    [
        "lsv2_pt_0123456789abcdef0123456789abcdef_0123456789",
        "sk-lf-1234",
        "pk-lf-1234",
        "sk-proj-abcdefghij0123456789",
        "a" * 8 + "0123456789abcdef0123456789abcdef",
    ],
)
def test_a_key_pasted_as_a_project_is_refused_and_never_stored(app, settings, project):
    for field in ("langsmith_project", "langfuse_project_id"):
        response = put_settings(app, {"tracing": {field: project}})
        assert response.status_code == 422
        assert project not in response.text
    assert project.encode() not in database_bytes(settings.data_dir)


@pytest.mark.parametrize(
    "project",
    [
        "bleep-prod-2026-eu-west-1",
        "cm1a2b3c4d5e6f7g8h9i0j1k2",
        "sk-team",
        "8f14e45fceea167a5a36dedd4bea2543",
    ],
)
def test_a_project_name_or_a_langfuse_project_id_is_kept(app, project):
    for field in ("langsmith_project", "langfuse_project_id"):
        response = put_settings(app, {"tracing": {field: project}})
        assert response.status_code == 200
        assert response.json()["tracing"][field] == project


def test_the_embeddings_address_is_not_cleared_while_the_endpoint_embeds(app):
    store = app.parts.settings_store
    put_secret(app, "EMBEDDINGS_BASE_URL", "http://host.docker.internal:1234/v1")
    put_settings(app, {"embeddings": {"provider": "openai", "model": "bge-m3"}})
    store.start_embeddings()  # the boot: the endpoint is the running embedder

    refused = app.delete("/api/secrets/EMBEDDINGS_BASE_URL")
    assert refused.status_code == 409 and "restart" in refused.json()["detail"]
    assert secrets(app)["EMBEDDINGS_BASE_URL"]["set"]


def test_the_embeddings_address_is_cleared_while_the_local_model_embeds(app):
    app.parts.settings_store.start_embeddings()
    put_secret(app, "EMBEDDINGS_BASE_URL", "http://host.docker.internal:1234/v1")
    assert app.delete("/api/secrets/EMBEDDINGS_BASE_URL").status_code == 200


def test_a_model_name_the_local_embedder_ignores_needs_no_restart(app):
    app.parts.settings_store.start_embeddings()
    response = put_settings(app, {"embeddings": {"model": "bge-m3"}})
    assert response.json()["restart_needed"] == []


def test_the_log_names_dashboard_values_the_env_now_decides(start, monkeypatch, caplog):
    app = start()
    put_settings(app, {"models": {"model": "openai/dashboard"}})
    put_secret(app, "EMBEDDINGS_BASE_URL", "http://host.docker.internal:1234/v1")
    put_secret(app, "EMBEDDINGS_API_KEY", EMBEDDINGS_KEY, address="EMBEDDINGS_BASE_URL")
    monkeypatch.setenv("LLM_MODEL", "openai/env-model")
    monkeypatch.setenv("EMBEDDINGS_BASE_URL", "http://192.168.1.20:11434/v1")
    caplog.set_level(logging.WARNING)
    start()
    assert "the dashboard's LLM_MODEL is not in force" in caplog.text
    assert "EMBEDDINGS_API_KEY in the secret file is not used" in caplog.text
    assert EMBEDDINGS_KEY not in caplog.text


def test_langfuse_switched_off_on_the_page_or_its_keys_cleared_stops_at_once(app):
    put_settings(app, {"tracing": {"langfuse_base_url": "https://cloud.example.test"}})
    put_secret(app, "LANGFUSE_PUBLIC_KEY", f"pk-lf-{uuid.uuid4().hex}")
    put_secret(app, "LANGFUSE_SECRET_KEY", f"sk-lf-{uuid.uuid4().hex}")
    on = {"langfuse_base_url": "https://cloud.example.test", "tracing": "langfuse"}
    assert put_settings(app, {"tracing": on}).status_code == 200
    assert isinstance(inner_tracer(app), LangfuseTracing)

    off = put_settings(app, {"tracing": {**on, "tracing": "off"}})
    assert off.json()["restart_needed"] == []
    assert isinstance(inner_tracer(app), NoTracing)

    assert put_settings(app, {"tracing": on}).json()["restart_needed"] == ["tracing"]
    assert isinstance(inner_tracer(app), NoTracing)  # the same key: waits for a restart
    assert app.delete("/api/secrets/LANGFUSE_SECRET_KEY").status_code == 200
    assert isinstance(inner_tracer(app), NoTracing)
    assert app.get("/api/settings").json()["restart_needed"] == []


def test_the_view_carries_each_providers_default_models_and_resolving_uses_them(app):
    defaults = app.get("/api/settings").json()["model_defaults"]

    assert defaults["openrouter"]["model"] == "openai/gpt-6-luna"
    assert defaults["anthropic"] == {
        "model": "claude-sonnet-5-5",
        "cheap_model": "claude-haiku-5-5",
    }
    resolved = ModelSettings(provider="anthropic").resolved()
    assert {"model": resolved.model, "cheap_model": resolved.cheap_model} == defaults["anthropic"]
