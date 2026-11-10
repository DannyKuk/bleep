import asyncio

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from bleep.main import create_app
from bleep.notify.notifier import Notifier
from bleep.wiring import build_parts


def test_the_lifespan_wires_one_approval_store_the_guard_log_and_the_reminders(
    settings, parts, monkeypatch
):
    flushed: list[bool] = []
    flush = parts.guard_log.flush

    async def counting_flush() -> None:
        flushed.append(True)
        await flush()

    monkeypatch.setattr(parts.guard_log, "flush", counting_flush)
    app = create_app(settings, parts)
    with TestClient(app):
        # One store for the graph, the runner (boot recovery of a decided approval), the API
        # and the reminders.
        assert parts.graph_deps.approvals is parts.approvals
        assert app.state.runner.approvals is parts.approvals
        assert parts.graph_deps.guard_log is parts.guard_log
        reminders = app.state.reminders
        assert reminders.approvals is parts.approvals
        assert reminders.notifier is parts.graph_deps.notifier
        assert reminders.notifier is parts.notify.notifier  # the one real notifier, not a Null
        assert app.state.runner.notifier is parts.notify.notifier  # announces new incidents
        assert isinstance(reminders.notifier, Notifier)
        task = app.state.reminder_task
        assert not task.done()
        assert not flushed
    assert task.cancelled()
    assert flushed == [True]


def test_the_app_starts_and_stops_cleanly_with_no_telegram_token(settings, parts):
    assert parts.notify.bot is None and parts.notify.notifier.telegram is None
    started: list[object] = []
    start = parts.notify.start
    parts.notify.start = lambda runner: (started.append(runner), start(runner))[1]  # type: ignore[method-assign]
    app = create_app(settings, parts)
    with TestClient(app):
        assert started == [app.state.runner]  # start was called ...
        assert parts.notify._task is None  # ... and made no poller


def test_the_telegram_bot_shares_the_store_and_polls_only_with_a_token(
    settings, tmp_path, monkeypatch
):
    from bleep.hosts.registry import Registry
    from bleep.notify.telegram import TelegramBot

    polled: list[bool] = []

    async def fake_poll(self) -> None:
        polled.append(True)

    monkeypatch.setattr(TelegramBot, "poll_forever", fake_poll)
    on = settings.model_copy(
        update={"telegram_bot_token": SecretStr("123:abc"), "telegram_chat_id": "42"}
    )
    parts = build_parts(on, Registry([]))
    app = create_app(on, parts)
    with TestClient(app):
        bot = parts.notify.bot
        assert bot is not None
        # One store for the runner, the API (it reads `parts.approvals`) and the bot.
        assert bot.approvals is parts.approvals is app.state.runner.approvals
        assert bot.runner is app.state.runner  # given to the bot at start
        assert parts.graph_deps.notifier is parts.notify.notifier
        assert app.state.reminders.notifier is parts.notify.notifier
        assert parts.notify.notifier.telegram is bot.client
        assert parts.notify.notifier.approvals is parts.approvals
    assert polled == [True]


def test_shutdown_stops_the_poller_and_runs_before_the_telegram_client_closes(
    settings, monkeypatch
):
    from bleep.hosts.registry import Registry
    from bleep.incidents.runner import IncidentRunner
    from bleep.notify.telegram import TelegramBot

    async def idle(self) -> None:
        await asyncio.sleep(3600)

    monkeypatch.setattr(TelegramBot, "poll_forever", idle)
    on = settings.model_copy(
        update={"telegram_bot_token": SecretStr("123:abc"), "telegram_chat_id": "42"}
    )
    parts = build_parts(on, Registry([]))
    http = parts.notify._http
    seen: dict[str, bool] = {}
    close = IncidentRunner.close

    async def checking_close(self) -> None:
        seen["client_open_while_runs_stop"] = not http.is_closed
        seen["poller_stopped_first"] = parts.notify._task is None
        await close(self)

    monkeypatch.setattr(IncidentRunner, "close", checking_close)
    with TestClient(create_app(on, parts)):
        assert not http.is_closed
    assert seen == {"client_open_while_runs_stop": True, "poller_stopped_first": True}
    assert http.is_closed


def test_a_startup_failure_closes_what_was_already_opened(settings, monkeypatch):
    from bleep.hosts.registry import Registry
    from bleep.watcher.manager import WatcherManager

    async def boom(self) -> None:
        raise RuntimeError("manager failed")

    monkeypatch.setattr(WatcherManager, "start", boom)
    on = settings.model_copy(
        update={"telegram_bot_token": SecretStr("123:abc"), "telegram_chat_id": "42"}
    )
    parts = build_parts(on, Registry([]))
    http = parts.notify._http
    with pytest.raises(RuntimeError, match="manager failed"), TestClient(create_app(on, parts)):
        pass
    assert http.is_closed


def test_memory_is_wired_into_the_graph_and_recall_is_offered(settings, parts):
    from bleep.tools.registry import ToolContext

    assert parts.graph_deps.memory is parts.memory
    ctx = ToolContext("i", "a", parts.registry)
    assert "recall_incidents" in {t.name for t in parts.graph_deps.tools.read_tools(ctx)}


def test_boot_re_embeds_memory_after_an_embedder_change(settings):
    from bleep.hosts.registry import Registry
    from bleep.incidents.repository import IncidentRepo
    from bleep.memory.embeddings import FakeEmbedder
    from bleep.schemas.graph import Report
    from bleep.schemas.incidents import Signal, Subject

    old = build_parts(settings, Registry([]), embedder=FakeEmbedder(name="fake-v1"))

    async def remember() -> None:
        await old.db.migrate()
        subject = Subject(kind="host", host_id="a")
        incident, _ = await IncidentRepo(old.db).open_or_attach(
            subject, [Signal(subject=subject, kind="disk", at=old.memory.clock())]
        )
        report = Report(title="disk on a", summary="s", cause="cache", outcome="o")
        await old.memory.add_report(incident, report)

    asyncio.run(remember())
    newer = FakeEmbedder(name="fake-v2")
    parts = build_parts(settings, Registry([]), embedder=newer)
    with TestClient(create_app(settings, parts)):
        assert newer.calls == [["disk on a. Cause: cache Action: nothing ran."]]


def test_a_failing_re_embed_does_not_stop_the_app(settings, parts, monkeypatch, caplog):
    async def broken() -> int:
        raise RuntimeError("no model")

    monkeypatch.setattr(parts.memory, "reembed", broken)
    with TestClient(create_app(settings, parts)) as client:
        assert client.get("/health").json() == {"status": "ok"}
    assert "re-embedding failed" in caplog.text


def _secrets(tmp_path, env: dict[str, str]):
    from bleep.store.secrets import SecretName, SecretStore

    names = [
        SecretName("EMBEDDINGS_BASE_URL", kind="address", carries="EMBEDDINGS_API_KEY"),
        SecretName("EMBEDDINGS_API_KEY"),
    ]
    return SecretStore(tmp_path / "secrets.json", names, env=env)


def test_the_embedder_follows_the_settings(tmp_path, caplog):
    from bleep.memory.embeddings import FastEmbedEmbedder, OpenAICompatEmbedder
    from bleep.schemas.settings import EmbeddingsChoice
    from bleep.wiring import build_embedder

    secrets = _secrets(
        tmp_path,
        {"EMBEDDINGS_BASE_URL": "http://host.docker.internal:1234/v1", "EMBEDDINGS_API_KEY": "k"},
    )
    assert build_embedder(EmbeddingsChoice(), secrets).name == "fastembed:BAAI/bge-small-en-v1.5"
    remote = EmbeddingsChoice(provider="openai", model="text-embedding-nomic-embed-text-v1.5")
    embedder = build_embedder(remote, secrets)
    assert isinstance(embedder, OpenAICompatEmbedder)
    assert embedder.name == "openai:text-embedding-nomic-embed-text-v1.5"
    # The model is the endpoint's: the local one is the model baked into the image.
    local = build_embedder(EmbeddingsChoice(model="x/y"), secrets)
    assert isinstance(local, FastEmbedEmbedder)
    assert local.name == "fastembed:BAAI/bge-small-en-v1.5"
    # An endpoint without its base URL is not refused at start (the dashboard may set it): the
    # local model embeds until it is set, and the log says so.
    assert isinstance(build_embedder(remote, _secrets(tmp_path, {})), FastEmbedEmbedder)
    assert "EMBEDDINGS_BASE_URL" in caplog.text


def test_a_half_configured_embeddings_endpoint_no_longer_stops_bleep(settings):
    from bleep.config import check_settings

    check_settings(settings.model_copy(update={"embeddings_provider": "openai"}))


def test_recalls_min_score_is_the_models_unless_the_settings_name_one(tmp_path):
    """bge-small keeps 0.5, a known endpoint model brings its own, EMBEDDINGS_MIN_SCORE wins
    (#306)."""
    from bleep.schemas.settings import EmbeddingsChoice
    from bleep.wiring import build_embedder

    secrets = _secrets(tmp_path, {"EMBEDDINGS_BASE_URL": "http://x/v1"})
    m3 = EmbeddingsChoice(provider="openai", model="bge-m3")
    other = EmbeddingsChoice(provider="openai", model="nomic-embed-text")

    assert build_embedder(EmbeddingsChoice(), secrets).min_score == 0.5
    assert build_embedder(m3, secrets).min_score == 0.35
    assert build_embedder(other, secrets).min_score == 0.5
    for chosen in (m3, other, EmbeddingsChoice()):
        told = chosen.model_copy(update={"min_score": 0.42})
        assert build_embedder(told, secrets).min_score == 0.42


def test_the_embeddings_settings_are_read_from_their_env_names(monkeypatch, tmp_path):
    from bleep.config import Settings

    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "openai")
    monkeypatch.setenv("EMBEDDINGS_BASE_URL", "http://x/v1")
    monkeypatch.setenv("EMBEDDINGS_MODEL", "m")
    monkeypatch.setenv("EMBEDDINGS_API_KEY", "k")
    monkeypatch.setenv("EMBEDDINGS_MIN_SCORE", "0.35")
    loaded = Settings(admin_password=SecretStr("p" * 12), secret=SecretStr("s"))

    assert loaded.embeddings_min_score == 0.35
    assert loaded.embeddings_provider == "openai"
    assert loaded.embeddings_base_url == "http://x/v1" and loaded.embeddings_model == "m"
    assert loaded.embeddings_api_key is not None
    assert loaded.embeddings_api_key.get_secret_value() == "k"


@pytest.mark.parametrize("value", ["1.5", "-0.1", "half"])
def test_a_min_score_outside_zero_to_one_is_refused(monkeypatch, value):
    from pydantic import ValidationError

    from bleep.config import Settings

    monkeypatch.setenv("EMBEDDINGS_MIN_SCORE", value)
    with pytest.raises(ValidationError, match="embeddings_min_score|EMBEDDINGS_MIN_SCORE"):
        Settings(admin_password=SecretStr("p" * 12), secret=SecretStr("s"))


def test_the_runner_traces_with_the_parts_tracer_and_it_is_flushed_at_shutdown(settings, parts):
    closed: list[bool] = []

    class Tracer:
        def run_config(self, incident):
            return {}

        def trace_url(self, incident):
            return None

        async def close(self) -> None:
            closed.append(True)

    parts.tracer = Tracer()
    app = create_app(settings, parts)
    with TestClient(app):
        assert app.state.runner.tracer is parts.tracer
        assert not closed
    assert closed == [True]


def test_tracing_to_langfuse_without_its_keys_refuses_to_start(settings, monkeypatch):
    from bleep.config import SettingsError
    from bleep.hosts.registry import Registry

    monkeypatch.setenv("TRACING", "langfuse")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    with pytest.raises(SettingsError, match="LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY"):
        build_parts(settings, Registry([]))


def test_the_github_client_reads_with_the_hosts_file_token(settings, tmp_path, monkeypatch):
    hosts = tmp_path / "hosts.yaml"
    hosts.write_text(
        "github:\n  token: {env: BLEEP_TEST_GITHUB_TOKEN}\n"
        "hosts:\n  - {id: demo, name: Demo, kind: socket, endpoint: 'tcp://127.0.0.1:1'}\n",
        encoding="utf-8",
    )
    with_file = settings.model_copy(update={"hosts_file": hosts})
    monkeypatch.setenv("BLEEP_TEST_GITHUB_TOKEN", "ghp_test")
    assert build_parts(with_file).github.authenticated
    monkeypatch.delenv("BLEEP_TEST_GITHUB_TOKEN")
    parts = build_parts(with_file)
    assert not parts.github.authenticated
    names = {s.name for s in parts.graph_deps.tools.specs()}
    assert {"recent_commits", "commit_diff"} <= names


def test_the_app_ingests_the_docker_docs_and_offers_knowledge_search(settings, parts):
    ingested = asyncio.Event()
    run = parts.knowledge_refresher.refresh_docker

    async def refresh_docker():
        result = await run()
        ingested.set()
        return result

    parts.knowledge_refresher.refresh_docker = refresh_docker  # type: ignore[method-assign]
    assert parts.knowledge_refresher.reports is parts.memory
    assert parts.knowledge_refresher.repos.client is parts.github  # type: ignore[union-attr]
    assert parts.knowledge_refresher.repos.registry is parts.registry  # type: ignore[union-attr]
    assert parts.knowledge.embedder is parts.memory.embedder  # one model for both
    assert "knowledge_search" in {s.name for s in parts.graph_deps.tools.specs()}
    with TestClient(create_app(settings, parts)) as client:
        client.portal.call(ingested.wait)
        hits = client.portal.call(parts.knowledge.search, "restart policy unless-stopped")
        pages = client.portal.call(parts.knowledge.document_ids, "docker:")
    assert hits and hits[0].source == "docker"
    assert len(pages) >= 20
