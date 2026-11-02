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
