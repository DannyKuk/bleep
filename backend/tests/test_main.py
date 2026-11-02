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
    with TestClient(create_app(settings, parts)):
        assert parts.notify._task is None  # no poller without a token


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
