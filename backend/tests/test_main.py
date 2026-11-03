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


def test_the_embedder_follows_the_settings(settings):
    from bleep.config import SettingsError, check_settings
    from bleep.memory.embeddings import FastEmbedEmbedder, OpenAICompatEmbedder
    from bleep.wiring import build_embedder

    assert build_embedder(settings).name == "fastembed:BAAI/bge-small-en-v1.5"
    remote = settings.model_copy(
        update={
            "embeddings_provider": "openai",
            "embeddings_base_url": "http://host.docker.internal:1234/v1",
            "embeddings_model": "text-embedding-nomic-embed-text-v1.5",
            "embeddings_api_key": SecretStr("sk-x"),
        }
    )
    check_settings(remote)
    embedder = build_embedder(remote)
    assert isinstance(embedder, OpenAICompatEmbedder)
    assert embedder.name == "openai:text-embedding-nomic-embed-text-v1.5"
    # EMBEDDINGS_MODEL is the endpoint's model: the local one is the model baked into the image.
    local = build_embedder(settings.model_copy(update={"embeddings_model": "x/y"}))
    assert isinstance(local, FastEmbedEmbedder)
    assert local.name == "fastembed:BAAI/bge-small-en-v1.5"
    half = settings.model_copy(update={"embeddings_provider": "openai"})
    with pytest.raises(SettingsError, match="EMBEDDINGS_BASE_URL"):
        check_settings(half)


def test_the_embeddings_settings_are_read_from_their_env_names(monkeypatch, tmp_path):
    from bleep.config import Settings

    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "openai")
    monkeypatch.setenv("EMBEDDINGS_BASE_URL", "http://x/v1")
    monkeypatch.setenv("EMBEDDINGS_MODEL", "m")
    monkeypatch.setenv("EMBEDDINGS_API_KEY", "k")
    loaded = Settings(admin_password=SecretStr("p" * 12), secret=SecretStr("s"))

    assert loaded.embeddings_provider == "openai"
    assert loaded.embeddings_base_url == "http://x/v1" and loaded.embeddings_model == "m"
    assert loaded.embeddings_api_key is not None
    assert loaded.embeddings_api_key.get_secret_value() == "k"
