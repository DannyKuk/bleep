import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from bleep.config import Settings
from bleep.guard.log import GuardLog
from bleep.incidents.approvals import ApprovalStore
from bleep.incidents.bus import EventBus
from bleep.incidents.repository import IncidentRepo
from bleep.notify import format as fmt
from bleep.notify.notifier import Notifier, setup
from bleep.notify.telegram import MessageLog, TelegramBot, TelegramClient, TelegramError
from bleep.schemas.graph import (
    Decision,
    Diagnosis,
    PlanStep,
    Proposal,
    Report,
    VerificationResult,
)
from bleep.schemas.incidents import Signal, Subject
from bleep.store.db import Database

FIXTURES = Path(__file__).parent.parent / "fixtures" / "telegram"
T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
TOKEN = "7001:AAH-secret_token-value"
CHAT = "4242"
PLAN = Proposal(
    kind="act",
    steps=[PlanStep(action="docker.restart", host_id="demo", target="shop-web-1")],
    reason="it exited (code 137).",
)


def fixture(name: str, **values: str) -> dict:
    text = (FIXTURES / f"{name}.json").read_text()
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return json.loads(text)


class FakeTelegram:
    """The Bot API as `httpx.MockTransport`: queued getUpdates answers, recorded calls."""

    def __init__(self) -> None:
        self.updates: list[dict | httpx.Response] = []
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.telegram.org"
        method = request.url.path.rsplit("/", 1)[1]
        body = json.loads(request.content)
        self.calls.append((method, body))
        if method == "getUpdates":
            queued = self.updates.pop(0) if self.updates else {"ok": True, "result": []}
            return (
                queued if isinstance(queued, httpx.Response) else httpx.Response(200, json=queued)
            )
        if method == "sendMessage":
            return httpx.Response(200, json=fixture("send_message_ok"))
        return httpx.Response(200, json={"ok": True, "result": True})

    def sent(self, method: str) -> list[dict]:
        return [body for name, body in self.calls if name == method]


class FakeRunner:
    def __init__(self) -> None:
        self.resumed: list[tuple[str, Decision]] = []

    async def resume(self, incident_id: str, decision: Decision) -> None:
        self.resumed.append((incident_id, decision))


class Env:
    db: Database
    api: FakeTelegram
    runner: FakeRunner
    approvals: ApprovalStore
    guard: GuardLog
    bus: EventBus
    bot: TelegramBot
    notifier: Notifier
    incident_id: str
    approval_id: str


@pytest.fixture
async def env(tmp_path) -> Env:
    e = Env()
    e.db = Database(tmp_path / "bleep.sqlite")
    await e.db.migrate()
    e.api, e.runner = FakeTelegram(), FakeRunner()
    e.approvals, e.guard, e.bus = ApprovalStore(e.db, "s"), GuardLog(e.db), EventBus(e.db)
    http = httpx.AsyncClient(transport=httpx.MockTransport(e.api))
    messages = MessageLog(e.db)

    async def no_sleep(_: float) -> None:
        return None

    e.bot = TelegramBot(
        TOKEN, CHAT, e.approvals, e.runner, http, messages=messages, guard_log=e.guard,
        clock=lambda: T0, sleep=no_sleep,
    )  # fmt: skip
    e.notifier = Notifier(e.bus, e.bot.client, CHAT, messages, e.approvals)
    subject = Subject(kind="service", host_id="demo", service_id="demo/shop")
    incident, _ = await IncidentRepo(e.db).open_or_attach(
        subject, [Signal(subject=subject, kind="container_died", at=T0)]
    )
    e.incident_id = incident.id
    e.incident = incident
    request = await e.approvals.request(incident.id, PLAN, [])
    e.approval_id = request.id
    # The proposal message the fixtures' message id 311 refers to.
    await e.notifier.proposal(incident, None, PLAN, request)
    e.api.calls.clear()
    return e


async def poll(env: Env, name: str) -> None:
    env.api.updates.append(fixture(name, APPROVAL_ID=env.approval_id))
    await env.bot.poll_once()


# --- decisions ---


async def test_button_press_resolves_and_resumes(env):
    await poll(env, "callback_approve")

    [(incident_id, decision)] = env.runner.resumed
    assert incident_id == env.incident_id
    assert (decision.kind, decision.via) == ("approve", "telegram")
    assert (await env.approvals.current(env.incident_id)).decision.kind == "approve"
    [pop_up] = env.api.sent("answerCallbackQuery")
    assert pop_up["text"] == "Approved. Bleep is carrying on."
    [cleared] = env.api.sent("editMessageReplyMarkup")
    assert cleared["message_id"] == 311 and cleared["reply_markup"] == {"inline_keyboard": []}


async def test_reject_button(env):
    await poll(env, "callback_reject")
    assert env.runner.resumed[0][1].kind == "reject"


async def test_reply_to_a_proposal_becomes_an_instruction(env):
    await poll(env, "reply_instruction")

    [(incident_id, decision)] = env.runner.resumed
    assert incident_id == env.incident_id
    assert (decision.kind, decision.text) == (
        "instruct",
        "Restart the database first, then the web container",
    )
    assert (await env.approvals.current(env.incident_id)).decision.kind == "instruct"
    [reply] = env.api.sent("sendMessage")
    assert reply["reply_parameters"] == {"message_id": 320}


async def test_text_that_is_not_a_reply_is_explained_and_decides_nothing(env):
    await poll(env, "plain_message")
    assert env.runner.resumed == []
    assert "reply to a proposal" in env.api.sent("sendMessage")[0]["text"]


async def test_instruction_over_the_cap_is_refused(env):
    update = fixture("reply_instruction")
    update["result"][0]["message"]["text"] = "x" * 1001
    env.api.updates.append(update)
    await env.bot.poll_once()
    assert env.runner.resumed == []
    assert "too long" in env.api.sent("sendMessage")[0]["text"]
    assert (await env.approvals.current(env.incident_id)).decision is None


async def test_loser_of_a_race_is_told_the_dashboard_won(env):
    await env.approvals.resolve(
        env.incident_id, Decision(kind="reject", via="dashboard", at=T0), env.approval_id
    )
    await poll(env, "callback_approve")

    assert env.runner.resumed == []
    assert env.api.sent("answerCallbackQuery")[0]["text"] == "Already decided on the dashboard"
    assert (await env.approvals.current(env.incident_id)).decision.kind == "reject"


async def test_second_tap_is_told_telegram_already_decided(env):
    await poll(env, "callback_approve")
    await poll(env, "callback_reject")
    assert len(env.runner.resumed) == 1
    assert env.api.sent("answerCallbackQuery")[1]["text"] == "Already decided on Telegram"


async def test_a_button_on_a_replaced_plan_wins_nothing(env):
    await env.approvals.resolve(
        env.incident_id, Decision(kind="instruct", text="x", via="dashboard", at=T0)
    )
    await env.approvals.request(env.incident_id, PLAN, [])  # the new plan
    await poll(env, "callback_approve")

    assert env.runner.resumed == []
    assert "replaced" in env.api.sent("answerCallbackQuery")[0]["text"]
    assert (await env.approvals.current(env.incident_id)).decision is None


async def test_a_button_on_an_unknown_message_is_refused(env):
    update = fixture("callback_approve", APPROVAL_ID=env.approval_id)
    update["result"][0]["callback_query"]["message"]["message_id"] = 9999
    env.api.updates.append(update)
    await env.bot.poll_once()
    assert env.runner.resumed == []
    assert "does not know" in env.api.sent("answerCallbackQuery")[0]["text"]


# --- other chats ---


@pytest.mark.parametrize("name", ["foreign_message", "foreign_callback"])
async def test_other_chats_are_ignored_and_logged(env, name):
    await poll(env, name)

    assert env.runner.resumed == []
    assert env.api.calls[1:] == []  # nothing sent back, not even a refusal
    assert (await env.approvals.current(env.incident_id)).decision is None
    [entry] = await env.guard.entries("telegram")
    assert entry.kind == "foreign_chat" and "999" in entry.detail
    assert "approve everything" not in entry.detail


# --- polling ---


async def test_poll_advances_the_offset(env):
    await poll(env, "plain_message")
    await env.bot.poll_once()
    first, second = env.api.sent("getUpdates")
    assert "offset" not in first and second["offset"] == 700005
    assert first["timeout"] == 30


async def test_poll_forever_backs_off_on_an_error_and_goes_on(env):
    sleeps: list[float] = []

    async def stop_on_third(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 3:
            raise KeyboardInterrupt  # ends the loop; not an Exception, so nothing swallows it

    env.bot._sleep = stop_on_third
    conflict = httpx.Response(409, json=fixture("conflict"))
    env.api.updates += [
        conflict,
        conflict,
        fixture("callback_approve", APPROVAL_ID=env.approval_id),
        conflict,
    ]
    with pytest.raises(KeyboardInterrupt):
        await env.bot.poll_forever()
    assert sleeps == [1.0, 2.0, 1.0]  # reset by the good poll
    assert len(env.runner.resumed) == 1


async def test_errors_never_carry_the_token(env):
    env.api.updates.append(httpx.Response(502, text="<html>bad gateway</html>"))
    with pytest.raises(TelegramError) as error:
        await env.bot.client.get_updates(None)
    assert TOKEN not in str(error.value)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    client = TelegramClient(TOKEN, httpx.AsyncClient(transport=httpx.MockTransport(refuse)))
    with pytest.raises(TelegramError) as error:
        await client.call("getMe")
    assert TOKEN not in str(error.value) and "ConnectError" in str(error.value)


# --- messages ---


async def test_proposal_message_escapes_markdown_and_carries_buttons(env):
    incident = env.incident.model_copy(update={"title": "shop_web [down] (1.2)"})
    diagnosis = Diagnosis(
        root_cause="DB_HOST=db-1 *refused* `x` > #1 ~ | {a}!", category="config", confidence=0.85
    )
    request = (await env.approvals.current(env.incident_id)).request
    await env.notifier.proposal(incident, diagnosis, PLAN, request)

    [sent] = env.api.sent("sendMessage")
    text = sent["text"]
    assert sent["parse_mode"] == "MarkdownV2"
    assert r"shop\_web \[down\] \(1\.2\)" in text
    assert r"DB\_HOST\=db\-1 \*refused\* \`x\` \> \#1 \~ \| \{a\}\!" in text
    assert r"docker\.restart shop\-web\-1 on demo" in text
    assert "85% sure" in text
    for char in "_*[]()~`>#+-=|{}.!":  # every special character in the message is escaped
        assert all(text[i - 1] == "\\" for i, c in enumerate(text) if c == char and i)
    buttons = sent["reply_markup"]["inline_keyboard"][0]
    assert [b["callback_data"] for b in buttons] == [
        f"approve:{env.approval_id}",
        f"reject:{env.approval_id}",
    ]
    assert all(len(b["callback_data"].encode()) <= 64 for b in buttons)


async def test_proposal_message_is_remembered_so_a_reply_finds_it(env):
    assert await MessageLog(env.db).get(311) == (env.incident_id, env.approval_id)


async def test_model_text_is_redacted_before_it_leaves(env):
    diagnosis = Diagnosis(
        root_cause="auth failed with Bearer abcdef1234567890abcdef",
        category="config",
        confidence=0.5,
    )
    await env.notifier.proposal(
        env.incident, diagnosis, PLAN, (await env.approvals.current(env.incident_id)).request
    )
    assert "abcdef1234567890abcdef" not in env.api.sent("sendMessage")[0]["text"]


def test_long_messages_are_cut_below_the_limit_without_a_dangling_escape():
    text = fmt.render("notice", id="inc_1", text="." * 5000)
    assert len(text) <= fmt.TELEGRAM_LIMIT
    body = text.removesuffix("…")
    assert (len(body) - len(body.rstrip("\\"))) % 2 == 0  # no lone backslash at the cut
    assert len(fmt.render("notice", id="i", text="a" * 5000)) < 700


async def test_reminder_resends_the_plan_with_buttons_and_tells_the_bus(env):
    request = (await env.approvals.current(env.incident_id)).request
    await env.notifier.reminder(env.incident, request)

    [sent] = env.api.sent("sendMessage")
    assert "Still waiting" in sent["text"] and sent["reply_markup"]
    assert [e.kind for e in await env.bus.history(env.incident_id)] == ["reminder"]


async def test_other_messages_are_sent_and_a_send_failure_does_not_raise(env):
    await env.notifier.incident_opened(env.incident)
    await env.notifier.verification(
        env.incident, VerificationResult(ok=True, detail="Answering 200.")
    )
    await env.notifier.report(env.incident, Report(summary="Restarted; healthy."))
    await env.notifier.notice(env.incident_id, "Cost cap reached.")
    assert len(env.api.sent("sendMessage")) == 4

    env.api.calls.clear()
    broken = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500, json={"ok": False}))
    )
    env.notifier.telegram = TelegramClient(TOKEN, broken)
    await env.notifier.notice(env.incident_id, "still fine")  # swallowed
    with pytest.raises(TelegramError):  # the Approval node reports this one to the dashboard
        await env.notifier.proposal(
            env.incident, None, PLAN, (await env.approvals.current(env.incident_id)).request
        )


# --- no token ---


async def test_without_a_token_the_notifier_is_bus_only(tmp_path):
    db = Database(tmp_path / "bleep.sqlite")
    await db.migrate()
    settings = Settings(
        data_dir=tmp_path, admin_password=SecretStr("x" * 12), secret=SecretStr("s"),
        TELEGRAM_BOT_TOKEN=None, TELEGRAM_CHAT_ID=None,
    )  # fmt: skip
    built = setup(settings, db, EventBus(db), ApprovalStore(db, "s"))
    assert built.bot is None

    subject = Subject(kind="service", host_id="demo", service_id="demo/shop")
    incident, _ = await IncidentRepo(db).open_or_attach(
        subject, [Signal(subject=subject, kind="container_died", at=T0)]
    )
    request = await ApprovalStore(db, "s").request(incident.id, PLAN, [])
    await built.notifier.incident_opened(incident)
    await built.notifier.proposal(incident, None, PLAN, request)
    await built.notifier.reminder(incident, request)
    await built.notifier.verification(incident, VerificationResult(ok=True))
    await built.notifier.report(incident, Report(summary="s"))
    await built.notifier.notice(incident.id, "n")
    built.start(FakeRunner())  # nothing to start
    await built.stop()


async def test_a_token_with_a_chat_id_builds_the_bot_and_stop_ends_it(tmp_path):
    db = Database(tmp_path / "bleep.sqlite")
    await db.migrate()
    settings = Settings(
        data_dir=tmp_path, admin_password=SecretStr("x" * 12), secret=SecretStr("s"),
        TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_CHAT_ID=CHAT,
    )  # fmt: skip
    built = setup(settings, db, EventBus(db), ApprovalStore(db, "s"))
    assert built.bot is not None and built.notifier.telegram is not None
    assert TOKEN not in repr(settings)
    built.bot.client.http = httpx.AsyncClient(transport=httpx.MockTransport(FakeTelegram()))
    built.start(FakeRunner())
    assert built._task is not None and not built._task.done()
    await built.stop()
    assert built._task is None
