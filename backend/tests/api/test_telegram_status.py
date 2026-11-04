"""The Telegram status over HTTP: whether it is set up and polling, never the token (#41)."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from bleep.config import Settings
from bleep.hosts.registry import Registry
from bleep.main import create_app
from bleep.memory.embeddings import FakeEmbedder
from bleep.wiring import build_parts

TOKEN = "7001:AAH-secret_token-value"
CHAT = "-1001234567890"
T0 = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)


def client_for(settings: Settings):
    """A client whose bot never polls, so no request leaves; a test sets its last poll."""
    parts = build_parts(settings, Registry([]), FakeEmbedder())
    parts.notify.start = lambda runner: None  # type: ignore[method-assign]
    return TestClient(create_app(settings, parts)), parts


@pytest.fixture
def telegram_settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        hosts_file=tmp_path / "hosts.yaml",
        admin_password=SecretStr("test-password"),
        secret=SecretStr("test-secret"),
        TELEGRAM_BOT_TOKEN=TOKEN,
        TELEGRAM_CHAT_ID=CHAT,
    )


def test_without_telegram_the_status_says_so(settings):
    client, _ = client_for(settings)
    with client:
        client.post("/api/auth/login", json={"password": "test-password"})
        body = client.get("/api/telegram/status").json()
    assert body == {
        "configured": False,
        "chat_id": None,
        "last_poll": None,
        "token": {"env": "TELEGRAM_BOT_TOKEN", "file": None, "set": False, "source": None},
    }


def test_with_telegram_the_status_masks_the_chat_and_shows_the_last_poll(telegram_settings):
    client, parts = client_for(telegram_settings)
    assert parts.notify.bot is not None
    parts.notify.bot.last_poll = T0
    with client:
        client.post("/api/auth/login", json={"password": "test-password"})
        response = client.get("/api/telegram/status")
    body = response.json()
    assert body["configured"] is True
    assert body["chat_id"] == "••••90"
    assert body["last_poll"] == "2026-11-03T10:00:00Z"
    assert body["token"] == {
        "env": "TELEGRAM_BOT_TOKEN",
        "file": None,
        "set": True,
        "source": "env",
    }
    assert TOKEN not in response.text and CHAT not in response.text


def test_the_status_needs_a_session(settings):
    client, _ = client_for(settings)
    with client:
        assert client.get("/api/telegram/status").status_code == 401
