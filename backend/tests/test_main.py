from fastapi.testclient import TestClient

from bleep.main import create_app


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
        task = app.state.reminder_task
        assert not task.done()
        assert not flushed
    assert task.cancelled()
    assert flushed == [True]
