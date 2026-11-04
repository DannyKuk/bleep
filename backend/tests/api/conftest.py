import asyncio

import pytest


@pytest.fixture(autouse=True)
def no_real_url_checks(monkeypatch):
    """The watcher the app starts must not make real HTTP requests or record results of its own."""

    async def never_answers(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr("bleep.watcher.http_checks.check_url", never_answers)
