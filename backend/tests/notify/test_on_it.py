"""🤫 I'm on it on an incident's Telegram opener (#389): 1 h, 4 h or until 08:00 snooze the
incident's subject through the quiet store, and the watcher's quiet listener ends it as Quiet."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from bleep.config import Settings
from bleep.guard.log import GuardLog
from bleep.hosts.registry import Registry
from bleep.incidents.approvals import ApprovalStore
from bleep.incidents.bus import EventBus
from bleep.incidents.quiet import QuietStore, quiet_target, snooze_until
from bleep.incidents.repository import IncidentRepo
from bleep.incidents.runner import IncidentRunner
from bleep.notify import format as fmt
from bleep.notify.notifier import Notifier, setup
from bleep.notify.telegram import MessageLog, OnIt, TelegramBot
from bleep.schemas.incidents import Signal, Subject
from bleep.schemas.settings import Thresholds
from bleep.store.db import Database
from bleep.watcher.detector import Detector
from bleep.watcher.manager import WatcherManager

FIXTURES = Path(__file__).parent.parent / "fixtures" / "telegram"
T0 = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
CHAT = "4242"
SHOP = Subject(kind="service", host_id="laptop", service_id="laptop/compose:shop")
HOST = Subject(kind="host", host_id="M1")
VIENNA = ZoneInfo("Europe/Vienna")


def fixture(name: str, **values: str) -> list[dict]:
    text = (FIXTURES / f"{name}.json").read_text()
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return json.loads(text)["result"]


class Chat:
    """The Bot API as `httpx.MockTransport`: every sent message gets the next id."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.next_id = 500
        self.updates: list[list[dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[1]
        body = json.loads(request.content)
        self.calls.append((method, body))
        if method == "getUpdates":
            result = self.updates.pop(0) if self.updates else []
            return httpx.Response(200, json={"ok": True, "result": result})
        if method == "sendMessage":
            self.next_id += 1
            return httpx.Response(200, json={"ok": True, "result": {"message_id": self.next_id}})
        return httpx.Response(200, json={"ok": True, "result": True})

    def sent(self, method: str) -> list[dict]:
        return [body for name, body in self.calls if name == method]


class NoResume:
    async def resume(self, incident_id, decision, approval_id) -> None:
        raise AssertionError("a snooze resumes no plan")


class Env:
    """The notifier and the bot over a recorded chat, with the real quiet path behind them: the
    quiet store tells the watcher manager, which ends the incident through the runner."""

    def __init__(self, tmp_path, zone=UTC) -> None:
        self.tmp_path = tmp_path
        self.zone = zone
        self.now = T0

    async def start(self) -> Env:
        clock = lambda: self.now  # noqa: E731
        self.db = Database(self.tmp_path / "bleep.sqlite")
        await self.db.migrate()
        self.chat = Chat()
        self.repo = IncidentRepo(self.db, clock=clock)
        self.bus = EventBus(self.db, clock)
        self.approvals = ApprovalStore(self.db, "s", clock=clock)
        self.quiet = QuietStore(self.db, clock)
        self.guard = GuardLog(self.db)
        self.messages = MessageLog(self.db)
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.chat))
        on_it = OnIt(self.repo, self.quiet, self.bus, self.zone)
        self.bot = TelegramBot(
            "7001:token", CHAT, self.approvals, NoResume(), http, messages=self.messages,
            guard_log=self.guard, clock=clock, on_it=on_it,
        )  # fmt: skip
        self.notifier = Notifier(
            self.bus, self.bot.client, CHAT, self.messages, self.approvals,
            incidents=self.repo, clock=clock, on_it=on_it,
        )  # fmt: skip
        self.repo.on_finished(self.notifier.incident_finished)
        runner = IncidentRunner(None, self.repo, self.bus, self.approvals, self.notifier)  # type: ignore[arg-type]
        self.manager = WatcherManager(
            Registry([]),
            Detector(Thresholds(), clock),
            self.repo,
            runner,
            self.bus,
            clock=clock,
            quiet=self.quiet,
            http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
        )
        self.quiet.subscribe(self.manager.quiet_changed)
        return self

    async def open(self, subject=SHOP, kind="container_died", name="shop"):
        signal = Signal(subject=subject, kind=kind, at=self.now, container="shop-web")
        incident, _ = await self.repo.open_or_attach(subject, [signal], name=name)
        await self.notifier.incident_opened(incident)
        return incident

    async def press(self, opener: int, choice: str | None = None) -> None:
        if choice is None:
            self.chat.updates.append(fixture("callback_on_it", MESSAGE_ID=str(opener)))
        else:
            update = fixture("callback_on_it_choice", MESSAGE_ID=str(opener), CHOICE=choice)
            self.chat.updates.append(update)
        await self.bot.poll_once()
        await self.notifier.drain()

    def answers(self) -> list[str]:
        return [body["text"] for body in self.chat.sent("answerCallbackQuery")]

    def keyboards(self) -> list[tuple[int, list[tuple[str, str]]]]:
        """Each edit of a message's buttons: the message and the buttons it shows now."""
        return [
            (body["message_id"], buttons(body)) for body in self.chat.sent("editMessageReplyMarkup")
        ]


def buttons(body: dict) -> list[tuple[str, str]]:
    rows = body.get("reply_markup", {}).get("inline_keyboard", [])
    return [(b["text"], b["callback_data"]) for row in rows for b in row]


def first_line(message: dict) -> str:
    return message["text"].split("\n", 1)[0]


@pytest.fixture
async def env(tmp_path) -> Env:
    return await Env(tmp_path).start()


# --- the button ---


async def test_the_opener_of_a_service_incident_carries_im_on_it(env):
    await env.open()

    [opener] = env.chat.sent("sendMessage")
    assert first_line(opener).startswith("*🔴 New: shop")
    assert buttons(opener) == [("🤫 I'm on it", "onit")]


async def test_the_opener_of_a_host_incident_carries_im_on_it(env):
    await env.open(HOST, kind="host_unreachable", name=None)

    [opener] = env.chat.sent("sendMessage")
    assert buttons(opener) == [("🤫 I'm on it", "onit")]


async def test_only_the_opener_carries_it(env):
    incident = await env.open()
    await env.notifier.notice(incident.id, "The model is not answering.")

    opener, note = env.chat.sent("sendMessage")
    assert buttons(opener) and not buttons(note)


async def test_a_service_incident_that_names_no_service_has_nothing_to_snooze(env):
    await env.open(Subject(kind="service", host_id="laptop"))

    [opener] = env.chat.sent("sendMessage")
    assert buttons(opener) == []


async def test_a_press_shows_the_three_choices_in_place_of_the_button(env):
    await env.open()

    await env.press(501)

    assert env.keyboards() == [
        (501, [("1 h", "onit:1h"), ("4 h", "onit:4h"), ("Until tomorrow 08:00", "onit:morning")])
    ]
    assert env.answers() == ["How long should Bleep leave it alone?"]
    assert await env.quiet.active() == []  # nothing is snoozed before a choice


# --- a choice ---


@pytest.mark.parametrize(
    ("choice", "until", "said"),
    [
        ("1h", T0 + timedelta(hours=1), "13:00"),
        ("4h", T0 + timedelta(hours=4), "16:00"),
        ("morning", datetime(2026, 11, 10, 8, 0, tzinfo=UTC), "tomorrow 08:00"),
    ],
)
async def test_each_choice_snoozes_the_service_and_ends_the_incident_as_quiet(
    env, choice, until, said
):
    incident = await env.open()
    await env.press(501)

    await env.press(501, choice)

    [state] = await env.quiet.active()
    assert (state.scope, state.target_id, state.mode) == ("service", SHOP.service_id, "snooze")
    assert state.until == until
    assert (await env.repo.get(incident.id)).status == "closed"
    opener, end = env.chat.sent("sendMessage")
    assert first_line(end) == fmt.bold_first_line(fmt.escape(f"🤫 Quiet until {said}"))
    assert "Closed: you snoozed" in end["text"]  # the watcher's quiet notice
    assert end["reply_parameters"]["message_id"] == 501
    assert env.answers()[-1] == f"Snoozed until {said}. Bleep leaves it alone until then."
    assert env.keyboards()[-1] == (501, [])  # the opener's buttons go when the incident ends


async def test_a_snooze_from_telegram_says_so_on_the_incidents_timeline(env):
    incident = await env.open()

    await env.press(501, "1h")

    notices = [e.payload["text"] for e in await env.bus.history(incident.id) if e.kind == "notice"]
    assert notices[0] == "Snoozed from Telegram: you said you are on it."
    assert notices[1].startswith("Closed: you snoozed")


async def test_a_host_incident_snoozes_the_host(env):
    incident = await env.open(HOST, kind="host_unreachable", name=None)

    await env.press(501, "4h")

    [state] = await env.quiet.active()
    assert (state.scope, state.target_id, state.until) == ("host", "M1", T0 + timedelta(hours=4))
    assert (await env.repo.get(incident.id)).status == "closed"
    _, end = env.chat.sent("sendMessage")
    assert first_line(end) == fmt.bold_first_line(fmt.escape("🤫 Quiet until 16:00"))


async def test_until_08_00_is_in_the_configured_time_zone(tmp_path):
    env = await Env(tmp_path, zone=VIENNA).start()
    await env.open()
    await env.press(501)  # 13:00 in Vienna

    await env.press(501, "morning")

    [state] = await env.quiet.active()
    assert state.until == datetime(2026, 11, 10, 7, 0, tzinfo=UTC)  # 08:00 CET
    _, end = env.chat.sent("sendMessage")
    assert first_line(end) == fmt.bold_first_line(fmt.escape("🤫 Quiet until tomorrow 08:00"))


async def test_in_the_small_hours_until_08_00_is_this_morning(tmp_path):
    env = await Env(tmp_path, zone=VIENNA).start()
    env.now = datetime(2026, 11, 9, 1, 30, tzinfo=UTC)  # 02:30 in Vienna
    await env.open()

    await env.press(501)
    await env.press(501, "morning")

    assert env.keyboards()[0][1][2] == ("Until 08:00", "onit:morning")
    [state] = await env.quiet.active()
    assert state.until == datetime(2026, 11, 9, 7, 0, tzinfo=UTC)


# --- presses that change nothing ---


async def test_a_press_after_the_incident_ended_is_answered_and_changes_nothing(env):
    incident = await env.open()
    await env.repo.set_status(incident.id, "resolved")
    await env.notifier.drain()
    edits = len(env.keyboards())

    await env.press(501, "1h")
    await env.press(501)

    assert await env.quiet.active() == []
    assert env.answers() == ["This incident has ended; nothing was snoozed."] * 2
    assert all(keys == [] for _, keys in env.keyboards()[edits:])  # never the choices again
    assert len(env.chat.sent("sendMessage")) == 1  # no second end message


async def test_a_second_choice_after_the_first_ended_the_incident_snoozes_nothing_more(env):
    await env.open()
    await env.press(501, "1h")

    await env.press(501, "4h")

    [state] = await env.quiet.active()
    assert state.until == T0 + timedelta(hours=1)
    assert env.answers()[-1] == "This incident has ended; nothing was snoozed."


async def test_a_press_from_another_chat_is_ignored_and_logged(env):
    await env.open()
    calls = len(env.chat.calls)
    env.chat.updates.append(fixture("foreign_callback_on_it", MESSAGE_ID="501"))

    await env.bot.poll_once()

    assert await env.quiet.active() == []
    assert env.chat.calls[calls + 1 :] == []  # not answered at all
    [entry] = await env.guard.entries("telegram")
    assert entry.kind == "foreign_chat" and "999" in entry.detail


async def test_a_press_while_a_step_runs_on_the_host_snoozes_nothing(env):
    incident = await env.open()
    await env.repo.set_status(incident.id, "executing")

    await env.press(501, "1h")

    assert await env.quiet.active() == []
    assert env.answers() == [
        "A step of the approved plan is running on the host. Try again once it is done."
    ]
    assert (await env.repo.get(incident.id)).status == "executing"


async def test_an_unknown_choice_snoozes_nothing(env):
    await env.open()

    await env.press(501, "forever")

    assert await env.quiet.active() == []
    assert env.answers() == ["Bleep does not know that message any more."]


async def test_a_press_on_a_message_bleep_never_logged_is_unknown(env):
    await env.press(777, "1h")

    assert await env.quiet.active() == []
    assert env.answers() == ["Bleep does not know that message any more."]


# --- the quiet end message ---


async def test_a_dashboard_snooze_ends_with_its_time_too_and_leave_alone_without(env):
    snoozed = await env.open()
    await env.quiet.set("service", SHOP.service_id, "snooze", T0 + timedelta(hours=1))
    blog = Subject(kind="service", host_id="laptop", service_id="laptop/compose:blog")
    left = await env.open(blog, name="blog")
    await env.quiet.set("service", blog.service_id, "leave_alone", None)

    ends = [m for m in env.chat.sent("sendMessage") if "🤫" in m["text"]]
    assert [first_line(m) for m in ends] == [
        fmt.bold_first_line(fmt.escape("🤫 Quiet until 13:00")),
        "*🤫 Quiet*",
    ]
    assert {(await env.repo.get(i.id)).status for i in (snoozed, left)} == {"closed"}


# --- the pure parts ---


def test_a_snooze_ends_as_the_dashboards_menu_has_it():
    noon = datetime(2026, 11, 9, 11, 0, tzinfo=UTC)  # 12:00 in Vienna
    assert snooze_until("1h", noon, VIENNA) == noon + timedelta(hours=1)
    assert snooze_until("4h", noon, VIENNA) == noon + timedelta(hours=4)
    assert snooze_until("morning", noon, VIENNA) == datetime(2026, 11, 10, 7, 0, tzinfo=UTC)
    exactly_eight = datetime(2026, 11, 9, 7, 0, tzinfo=UTC)
    assert snooze_until("morning", exactly_eight, VIENNA) == datetime(
        2026, 11, 10, 7, 0, tzinfo=UTC
    )


def test_until_08_00_across_a_clock_change_is_08_00_local():
    before_spring = datetime(2027, 3, 27, 20, 0, tzinfo=UTC)  # the night clocks go forward
    assert snooze_until("morning", before_spring, VIENNA) == datetime(
        2027, 3, 28, 6, 0, tzinfo=UTC
    )  # 08:00 CEST


def test_the_quiet_target_is_the_service_or_the_host():
    assert quiet_target(SHOP) == ("service", "laptop/compose:shop")
    assert quiet_target(HOST) == ("host", "M1")
    assert quiet_target(Subject(kind="service", host_id="laptop")) is None


def test_a_time_reads_as_today_tomorrow_or_a_weekday():
    now = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)  # a Monday
    assert fmt.when(now + timedelta(hours=2), now, UTC) == "14:00"
    assert fmt.when(datetime(2026, 11, 10, 8, 0, tzinfo=UTC), now, UTC) == "tomorrow 08:00"
    assert fmt.when(datetime(2026, 11, 12, 8, 0, tzinfo=UTC), now, UTC) == "Thu 08:00"
    late = datetime(2026, 11, 9, 23, 30, tzinfo=UTC)  # 00:30 on Tuesday in Vienna
    assert fmt.when(late, now, VIENNA) == "tomorrow 00:30"


# --- set up from the settings ---


def settings(tmp_path, **values) -> Settings:
    return Settings(
        data_dir=tmp_path, admin_password=SecretStr("x" * 12), secret=SecretStr("s"),
        TELEGRAM_BOT_TOKEN="7001:token", TELEGRAM_CHAT_ID=CHAT, **values,
    )  # fmt: skip


async def test_setup_gives_the_notifier_and_the_bot_the_button_in_the_configured_zone(tmp_path):
    db = Database(tmp_path / "bleep.sqlite")
    await db.migrate()
    repo, quiet, bus = IncidentRepo(db), QuietStore(db), EventBus(db)

    built = setup(
        settings(tmp_path, timezone="Europe/Vienna"), db, bus, ApprovalStore(db, "s"),
        incidents=repo, quiet=quiet,
    )  # fmt: skip

    assert built.bot is not None and built.bot.on_it is built.notifier.on_it
    assert built.notifier.on_it == OnIt(repo, quiet, bus, VIENNA)
    assert built.notifier.incidents is repo
    await built.close()


def test_the_time_zone_is_utc_unless_set_and_must_be_a_real_one(tmp_path):
    assert str(settings(tmp_path).zone) == "UTC"
    assert settings(tmp_path, timezone="Europe/Vienna").zone == VIENNA
    for wrong in ("Mars/Olympus", "../etc/passwd", "Europe"):
        with pytest.raises(ValidationError, match="IANA time zone"):
            settings(tmp_path, timezone=wrong)
