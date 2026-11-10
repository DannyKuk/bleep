"""Every time is stored in UTC and shown in the zone chosen on the settings page (#400): a snooze
set in Vienna is stored as UTC, and after the zone changes Telegram and the API both give the
right local time, without a restart."""

import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from datetime import time as clock_time
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from bleep.config import Settings
from bleep.hosts.registry import Registry
from bleep.main import create_app
from bleep.memory.embeddings import FakeEmbedder
from bleep.notify import format as fmt
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.incidents import Signal, Subject
from bleep.wiring import build_parts
from fakes import FakeEngine, compose_labels, socket_host

VIENNA = ZoneInfo("Europe/Vienna")
NEW_YORK = ZoneInfo("America/New_York")
SHOP, API = "a/compose:shop", "a/compose:api"


class Chat:
    """The Bot API as `httpx.MockTransport`, keeping the messages sent."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path.endswith("/sendMessage"):
            self.sent.append(body["text"])
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(self.sent)}})
        return httpx.Response(200, json={"ok": True, "result": True})


def box(name: str, project: str) -> ContainerSummary:
    return ContainerSummary(
        id=f"id-{name}", name=name, image="img", state="running", labels=compose_labels(project)
    )


@pytest.fixture
def chat() -> Chat:
    return Chat()


@pytest.fixture
def app(tmp_path, chat):
    """The app with Telegram on a recorded chat (never polled) and BLEEP_TIMEZONE=Europe/Vienna,
    the starting value before a zone is chosen on the dashboard."""
    settings = Settings(
        data_dir=tmp_path / "data",
        hosts_file=tmp_path / "hosts.yaml",
        admin_password=SecretStr("test-password"),
        secret=SecretStr("test-secret"),
        TELEGRAM_BOT_TOKEN="7001:token",
        TELEGRAM_CHAT_ID="4242",
        timezone="Europe/Vienna",
    )
    engine = FakeEngine("a", [box("shop-web-1", "shop"), box("api-1", "api")])

    async def connect(_host):
        return None, engine

    parts = build_parts(settings, Registry([socket_host(id="a")], connect), FakeEmbedder())
    parts.notify.start = lambda runner: None  # type: ignore[method-assign]
    assert parts.notify.bot is not None
    parts.notify.bot.client.http = httpx.AsyncClient(transport=httpx.MockTransport(chat))
    with TestClient(create_app(settings, parts)) as client:
        assert client.post("/api/auth/login", json={"password": "test-password"}).status_code == 204
        client.parts = parts  # type: ignore[attr-defined]
        client.db_path = settings.data_dir / "bleep.db"  # type: ignore[attr-defined]
        yield client


def open_incident(app, service_id: str, name: str) -> None:
    subject = Subject(kind="service", host_id="a", service_id=service_id)
    signal = Signal(subject=subject, kind="container_died", at=datetime.now(UTC))
    incident, _ = app.portal.call(app.parts.incidents.open_or_attach, subject, [signal], None, name)
    app.portal.call(app.parts.notify.notifier.incident_opened, incident)


def snooze(app, service_id: str, until: datetime) -> dict:
    response = app.put(
        f"/api/quiet/services/{service_id}", json={"mode": "snooze", "until": until.isoformat()}
    )
    assert response.status_code == 200
    return response.json()


def last_message(chat: Chat, count: int) -> str:
    """The first line of the `count`th message, once the quiet listener has sent it."""
    deadline = time.monotonic() + 5
    while len(chat.sent) < count and time.monotonic() < deadline:
        time.sleep(0.02)
    return chat.sent[count - 1].split("\n", 1)[0]


def stored_until(app, service_id: str) -> str:
    with sqlite3.connect(app.db_path) as db:
        [(until,)] = db.execute(
            "SELECT until FROM quiet WHERE scope = 'service' AND target_id = ?", (service_id,)
        ).fetchall()
    return until


def quiet_until(app, service_id: str) -> datetime:
    service = next(s for s in app.get("/api/services").json() if s["id"] == service_id)
    return datetime.fromisoformat(service["quiet"]["until"])


def test_a_snooze_set_in_vienna_is_stored_in_utc_and_shown_in_the_zone_chosen_later(app, chat):
    day = datetime.now(VIENNA).date() + timedelta(days=2)  # never today or tomorrow, anywhere
    eight_in_vienna = datetime.combine(day, clock_time(8), tzinfo=VIENNA)
    in_utc = eight_in_vienna.astimezone(UTC)
    assert app.get("/api/settings").json()["timezone"] == "Europe/Vienna"  # from BLEEP_TIMEZONE

    open_incident(app, SHOP, "shop")
    body = snooze(app, SHOP, eight_in_vienna)

    assert datetime.fromisoformat(body["until"]) == in_utc
    assert datetime.fromisoformat(body["until"]).utcoffset() == timedelta(0)
    assert stored_until(app, SHOP) == in_utc.isoformat()
    assert last_message(chat, 2) == fmt.bold_first_line(
        fmt.escape(f"🤫 Quiet until {day:%a} 08:00")
    )

    version = app.get("/api/settings").json()["version"]
    changed = app.put(
        "/api/settings",
        json={"general": {"timezone": "America/New_York"}},
        headers={"If-Match": f'"{version}"'},
    )
    assert changed.json()["timezone"] == "America/New_York"

    assert stored_until(app, SHOP) == in_utc.isoformat()  # nothing stored changed
    shown = quiet_until(app, SHOP).astimezone(ZoneInfo(changed.json()["timezone"]))
    assert shown == in_utc.astimezone(NEW_YORK)  # 02:00 there, as the dashboard shows it
    open_incident(app, API, "api")
    snooze(app, API, in_utc)
    in_new_york = in_utc.astimezone(NEW_YORK)
    assert last_message(chat, 4) == fmt.bold_first_line(
        fmt.escape(f"🤫 Quiet until {in_new_york:%a %H:%M}")
    )


def test_german_chosen_on_the_settings_page_is_what_telegram_says_next(app, chat):
    """The setting of #374 reaches Telegram's messages and buttons, without a restart (#375)."""
    open_incident(app, SHOP, "shop")
    assert chat.sent[0].startswith(fmt.bold_first_line(fmt.escape("🔴 New: shop #1 · a")))

    version = app.get("/api/settings").json()["version"]
    changed = app.put(
        "/api/settings", json={"general": {"language": "de"}}, headers={"If-Match": f'"{version}"'}
    )
    assert changed.json()["language"] == "de"
    open_incident(app, API, "api")

    assert chat.sent[1].startswith(fmt.bold_first_line(fmt.escape("🔴 Neu: api #1 · a")))
    assert fmt.escape("Ich schaue es mir an") in chat.sent[1]
    assert app.parts.notify.bot.actions.language() == "de"  # the bot's answers to a press too
