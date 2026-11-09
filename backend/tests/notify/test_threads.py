"""One Telegram thread per incident (#380): the opener, replies to it, one end message, Again."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from bleep import prompts
from bleep.config import Settings
from bleep.graph.nodes.report import HEALTHY_ANYWAY, OUTCOME_TEXT
from bleep.incidents.approvals import ApprovalStore
from bleep.incidents.bus import EventBus
from bleep.incidents.repository import IncidentRepo
from bleep.notify.notifier import Notifier, setup
from bleep.notify.telegram import MessageLog, TelegramBot
from bleep.schemas.graph import (
    Check,
    Decision,
    PlanStep,
    Proposal,
    Report,
    VerificationResult,
)
from bleep.schemas.incidents import Signal, Subject
from bleep.store.db import Database

T0 = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
CHAT = "4242"
SHOP = Subject(kind="service", host_id="laptop", service_id="laptop/compose:shop")
HOST = Subject(kind="host", host_id="M1")
PLAN = Proposal(
    kind="act",
    steps=[
        PlanStep(action="docker.start", host_id="laptop", target="shop-db"),
        PlanStep(action="docker.restart", host_id="laptop", target="shop-web"),
    ],
    reason="shop-web needs its database.",
)
END_EMOJI = ("✅", "⚠️", "❔", "🙅", "🛑 *Stopped", "🤫", "👍", "❗", "➖")


class Chat:
    """The Bot API as `httpx.MockTransport`: every sent message gets the next id."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.delivered: list[dict] = []
        self.next_id = 500
        self.down = False
        self.updates: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[1]
        body = json.loads(request.content)
        self.calls.append((method, body))
        if method == "getUpdates":
            result = self.updates.pop(0) if self.updates else []
            return httpx.Response(200, json={"ok": True, "result": result})
        if method == "sendMessage":
            if self.down:
                return httpx.Response(502, json={"ok": False, "description": "Bad Gateway"})
            self.next_id += 1
            self.delivered.append({**body, "id": self.next_id})
            return httpx.Response(200, json={"ok": True, "result": {"message_id": self.next_id}})
        return httpx.Response(200, json={"ok": True, "result": True})

    def sent(self) -> list[dict]:
        """The messages that reached the chat, in order, with the id each got."""
        return list(self.delivered)


class Runner:
    def __init__(self) -> None:
        self.resumed: list[tuple[str, Decision, str]] = []

    async def resume(self, incident_id: str, decision: Decision, approval_id: str) -> None:
        self.resumed.append((incident_id, decision, approval_id))


class Env:
    def __init__(self, tmp_path) -> None:
        self.tmp_path = tmp_path
        self.now = T0

    async def start(self, dashboard_url: str | None = "https://bleep.example") -> Env:
        self.db = Database(self.tmp_path / "bleep.sqlite")
        await self.db.migrate()
        self.chat, self.runner = Chat(), Runner()
        self.repo = IncidentRepo(self.db, clock=lambda: self.now)
        self.bus, self.approvals = (
            EventBus(self.db),
            ApprovalStore(self.db, "s", clock=lambda: self.now),
        )
        self.messages = MessageLog(self.db)
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.chat))
        self.bot = TelegramBot(
            "7001:token", CHAT, self.approvals, self.runner, http, messages=self.messages,
            clock=lambda: self.now,
        )  # fmt: skip
        self.notifier = Notifier(
            self.bus, self.bot.client, CHAT, self.messages, self.approvals,
            incidents=self.repo, dashboard_url=dashboard_url, clock=lambda: self.now,
        )  # fmt: skip
        return self

    async def open(self, subject=SHOP, kind="container_died", container="shop-web", name=None):
        signal = Signal(subject=subject, kind=kind, at=self.now, container=container)
        incident, _ = await self.repo.open_or_attach(subject, [signal], name=name)
        return incident

    async def close(self, incident, status="closed"):
        await self.repo.set_status(incident.id, status)
        return await self.repo.get(incident.id)

    async def ask(self, incident, plan=PLAN):
        request = await self.approvals.request(incident.id, plan, [])
        await self.notifier.proposal(incident, None, plan, request)
        return request

    async def reply(self, to_message: int, text: str) -> None:
        update = {
            "update_id": 9000 + len(self.chat.calls),
            "message": {
                "message_id": 9000 + len(self.chat.calls),
                "chat": {"id": int(CHAT), "type": "private"},
                "reply_to_message": {"message_id": to_message},
                "text": text,
            },
        }
        self.chat.updates.append([update])
        await self.bot.poll_once()


def report(outcome: str, **extra) -> Report:
    fields = {"title": "t", "summary": "s", "cause": "shop-db was stopped.", **extra}
    return Report(outcome=outcome, **fields)


def first_line(message: dict) -> str:
    return message["text"].split("\n", 1)[0]


def replied_to(message: dict) -> int | None:
    params = message.get("reply_parameters")
    if params is None:
        return None
    assert params["allow_sending_without_reply"] is True
    return params["message_id"]


def ends(messages: list[dict]) -> list[dict]:
    return [m for m in messages if any(m["text"].count(e) for e in END_EMOJI)]


@pytest.fixture
async def env(tmp_path) -> Env:
    return await Env(tmp_path).start()


# --- the short name ---


async def test_the_short_name_is_the_service_and_a_running_number_per_subject(env):
    first = await env.open(name="shop")
    await env.close(first)
    second = await env.open(name="shop")
    other = await env.open(
        Subject(kind="service", host_id="laptop", service_id="laptop/compose:blog")
    )
    host = await env.open(HOST, kind="host_unreachable", container=None)

    assert (first.short_name, second.short_name) == ("shop #1", "shop #2")
    assert other.short_name == "blog #1"  # no name given: the key of its service id
    assert host.short_name == "M1 #1"  # the hosts file's id
    assert (await env.repo.get(second.id)).short_name == "shop #2"  # stored, never recomputed


async def test_a_name_that_is_not_plain_is_quoted_as_in_titles(env):
    incident = await env.open(name="My shop\nhttps://evil.example")
    assert incident.short_name.startswith('"My shop') and incident.short_name.endswith('" #1')
    assert "\n" not in incident.short_name


# --- a fixed incident ---


async def test_a_fixed_incident_is_one_thread_from_new_to_fixed(env):
    incident = await env.open(name="shop")
    await env.notifier.incident_opened(incident)
    request = await env.ask(incident)
    env.now = T0 + timedelta(minutes=4)
    result = VerificationResult(
        ok=True,
        checks=[
            Check(kind="container", target="shop-db", ok=True, detail="running"),
            Check(kind="url", target="http://shop/health", ok=True, detail="answering 200"),
        ],
    )
    await env.bus.publish(incident.id, "verification", {"ok": True, "attempt": 1, "checks": [
        c.model_dump(mode="json") for c in result.checks
    ]})  # fmt: skip
    await env.notifier.verification(incident, result)
    await env.notifier.report(
        incident,
        report(
            OUTCOME_TEXT["resolved"],
            action_taken="docker.start shop-db on laptop: done",
            follow_ups=["Find out why shop-db stopped"],
        ),
    )

    opener, proposal, end = env.chat.sent()  # the verification merged into the end message
    assert first_line(opener) == "*🔴 New: shop \\#1 · laptop*"
    assert "shop\\-web died\\." in opener["text"]
    assert replied_to(opener) is None
    assert first_line(proposal) == "*🙋 Your call*" and replied_to(proposal) == opener["id"]
    assert "docker\\.start shop\\-db on laptop" in proposal["text"]
    assert first_line(end) == "*✅ Fixed in 4 min*" and replied_to(end) == opener["id"]
    assert "Cause: shop\\-db was stopped\\." in end["text"]
    assert "Ran: docker\\.start shop\\-db on laptop: done" in end["text"]
    assert "Checked: shop\\-db running; http://shop/health answering 200" in end["text"]
    assert (
        f"Details: https://bleep\\.example/incidents/{incident.id}".replace("_", "\\_")
        in end["text"]
    )
    assert "Decision" not in end["text"] and "inc\\_" not in opener["text"]
    assert ends(env.chat.sent()) == [end]
    assert request is not None


async def test_a_fix_that_did_not_hold_says_so_and_is_no_end(env):
    incident = await env.open()
    await env.notifier.incident_opened(incident)
    failed = VerificationResult(
        ok=False, checks=[Check(kind="container", target="shop-web", ok=False, detail="exited")]
    )
    await env.notifier.verification(incident, failed)

    opener, held = env.chat.sent()
    assert first_line(held) == "*❌ Didn't hold*" and replied_to(held) == opener["id"]
    assert ends(env.chat.sent()) == []


async def test_healthy_again_without_a_step_that_worked_is_not_fixed(env):
    incident = await env.open()
    await env.notifier.incident_opened(incident)
    env.now = T0 + timedelta(hours=2)
    await env.notifier.report(incident, report(HEALTHY_ANYWAY))

    _, end = env.chat.sent()
    assert first_line(end) == "*✅ Healthy again in 2 h*" and "Fixed" not in end["text"]


# --- no action, and its repeat ---


async def test_a_no_action_incident_ends_needing_you_with_at_most_three_next_steps(tmp_path):
    env = await Env(tmp_path).start(dashboard_url=None)
    incident = await env.open(kind="restart_loop")
    await env.notifier.incident_opened(incident)
    follow_ups = ["Set DATABASE_URL", "Run docker compose up -d shop-web", "Check", "Fourth"]
    await env.notifier.report(incident, report(OUTCOME_TEXT["no_action"], follow_ups=follow_ups))

    opener, end = env.chat.sent()
    assert "shop\\-web keeps restarting\\." in opener["text"]
    assert first_line(end) == "*⚠️ Needs you · no action*" and replied_to(end) == opener["id"]
    assert "Next:\n\\- Set DATABASE\\_URL\n" in end["text"] and "Fourth" not in end["text"]
    assert "Ran: nothing" in end["text"]
    assert "Details" not in end["text"]  # no dashboard address set


async def test_a_repeat_within_a_day_opens_as_again_quoting_how_the_last_one_ended(env):
    first = await env.open(name="shop")
    await env.notifier.incident_opened(first)
    await env.notifier.report(first, report(OUTCOME_TEXT["no_action"]))
    await env.close(first)
    env.now = T0 + timedelta(hours=1)
    second = await env.open(name="shop")
    assert second.fold_of is not None  # the same key within the fold window
    await env.notifier.report(second, report(OUTCOME_TEXT["no_action"]))  # no opener: folded

    _, first_end, again = env.chat.sent()
    assert first_line(again) == "*🔁 Again: shop \\#2 · 1 h after shop \\#1*"
    assert again["text"].split("\n")[1] == "shop\\-web died\\."
    assert "*⚠️ Needs you · no action*" in again["text"]  # it is its own end too
    assert replied_to(again) == first_end["id"]
    assert "seen" not in again["text"]


async def test_a_repeat_of_another_kind_within_a_day_is_again_with_its_own_opener(env):
    first = await env.open(kind="restart_loop")
    await env.notifier.incident_opened(first)
    await env.close(first)
    env.now = T0 + timedelta(minutes=2)
    second = await env.open(kind="unhealthy")
    assert second.fold_of is None
    await env.notifier.incident_opened(second)
    request = await env.ask(second)

    first_opener, again, proposal = env.chat.sent()
    assert first_line(again).startswith("*🔁 Again: shop \\#2 · 2 min after shop \\#1")
    assert replied_to(again) == first_opener["id"]  # the first one's last message
    assert replied_to(proposal) == again["id"]
    assert request is not None


async def test_an_incident_after_more_than_a_day_is_new(env):
    first = await env.open(kind="restart_loop")
    await env.close(first)
    env.now = T0 + timedelta(hours=25)
    second = await env.open(kind="unhealthy")
    await env.notifier.incident_opened(second)

    [opener] = env.chat.sent()
    assert first_line(opener).startswith("*🔴 New: shop \\#2") and replied_to(opener) is None


# --- a rejected plan ---


async def test_a_rejected_plan_ends_left_to_you(env):
    incident = await env.open()
    await env.notifier.incident_opened(incident)
    await env.ask(incident)
    await env.notifier.report(incident, report(OUTCOME_TEXT["rejected"]))

    opener, _, end = env.chat.sent()
    assert first_line(end) == "*🙅 Left to you*" and replied_to(end) == opener["id"]
    assert "Nothing was changed" in end["text"]


async def test_a_reminder_says_how_long_it_has_waited(env):
    incident = await env.open()
    await env.notifier.incident_opened(incident)
    request = await env.ask(incident)
    env.now = T0 + timedelta(minutes=30)
    await env.notifier.reminder(incident, request)

    opener, _, reminder = env.chat.sent()
    assert first_line(reminder) == "*⏰ Still waiting for you · 30 min*"
    assert replied_to(reminder) == opener["id"]
    assert [b["text"] for b in reminder["reply_markup"]["inline_keyboard"][0]] == [
        "Approve",
        "Reject",
    ]


# --- a host that comes back ---


async def test_a_host_that_comes_back_ends_with_back(env):
    incident = await env.open(HOST, kind="host_unreachable", container=None)
    await env.notifier.incident_opened(incident.model_copy(update={"status": "waiting"}))
    env.now = T0 + timedelta(minutes=6)
    await env.notifier.notice(incident.id, "Closed: the host answers again.", ending="back")

    opener, back = env.chat.sent()
    assert first_line(opener) == "*🔴 New: M1 \\#1 · host*"
    assert "M1 does not answer over SSH\\." in opener["text"]
    assert first_line(back) == "*✅ Back after 6 min*" and replied_to(back) == opener["id"]


@pytest.mark.parametrize(
    ("ending", "head"),
    [
        ("quiet", "*🤫 Quiet*"),
        ("expected", "*👍 Expected*"),
        ("error", "*❗ Stopped on an error*"),
        ("removed", "*➖ Closed: host removed*"),
    ],
)
async def test_every_other_close_is_its_own_end_message(env, ending, head):
    incident = await env.open()
    await env.notifier.incident_opened(incident)
    await env.notifier.notice(incident.id, "Closed: some reason.", ending=ending)

    opener, end = env.chat.sent()
    assert first_line(end) == head and replied_to(end) == opener["id"]
    assert ends(env.chat.sent()) == [end]


async def test_a_note_in_the_middle_of_a_run_is_no_end(env):
    incident = await env.open()
    await env.notifier.incident_opened(incident)
    await env.notifier.notice(incident.id, "The model is not answering.")

    opener, note = env.chat.sent()
    assert note["text"].startswith("ℹ️ The model is not answering") and not ends([note])
    assert replied_to(note) == opener["id"]


# --- replies ---


async def test_a_reply_to_a_message_without_a_plan_instructs_the_plan_that_waits(env):
    incident = await env.open()
    await env.notifier.incident_opened(incident)
    request = await env.ask(incident)
    opener = env.chat.sent()[0]

    await env.reply(opener["id"], "Don't restart, I'll change the batch size myself")

    [(incident_id, decision, approval_id)] = env.runner.resumed
    assert (incident_id, approval_id) == (incident.id, request.id)
    assert decision.kind == "instruct" and decision.text.startswith("Don't restart")


async def test_a_reply_to_a_message_without_a_plan_when_none_waits_decides_nothing(env):
    incident = await env.open()
    await env.notifier.incident_opened(incident)
    opener = env.chat.sent()[0]

    await env.reply(opener["id"], "restart it")

    assert env.runner.resumed == []
    assert env.chat.sent()[-1]["text"] == "Nothing is waiting for a decision on that incident."


async def test_a_reply_to_a_closed_incident_stays_with_it(env):
    first = await env.open()
    await env.notifier.incident_opened(first)
    await env.close(first)
    second = await env.open(kind="unhealthy")
    await env.ask(second)
    first_opener = env.chat.sent()[0]

    await env.reply(first_opener["id"], "restart it")

    assert env.runner.resumed == []  # never the other incident's plan
    assert env.chat.sent()[-1]["text"] == "Nothing is waiting for a decision on that incident."


async def test_a_reply_to_a_replaced_plan_is_still_stale(env):
    incident = await env.open()
    await env.notifier.incident_opened(incident)
    await env.ask(incident)
    old_proposal = env.chat.sent()[-1]
    await env.approvals.resolve(
        incident.id, Decision(kind="instruct", text="x", via="dashboard", at=T0), None
    )
    await env.ask(incident)

    await env.reply(old_proposal["id"], "do it differently")

    assert env.runner.resumed == []
    assert env.chat.sent()[-1]["text"] == "That plan has been replaced. Use the newest message."


# --- a missing opener ---


async def test_when_the_opener_never_arrived_the_next_message_becomes_the_opener(env):
    incident = await env.open()
    env.chat.down = True
    await env.notifier.incident_opened(incident)  # lost: Telegram is down
    env.chat.down = False
    await env.ask(incident)
    await env.notifier.notice(incident.id, "Planning again.")

    proposal, note = env.chat.sent()
    assert first_line(proposal) == "*🔴 New: shop \\#1 · laptop*"
    assert proposal["text"].split("\n")[1] == "shop\\-web died\\."
    assert "*🙋 Your call*" in proposal["text"] and replied_to(proposal) is None
    assert replied_to(note) == proposal["id"]


async def test_buttons_are_cleared_only_on_messages_that_have_them(env):
    incident = await env.open()
    await env.notifier.incident_opened(incident)
    await env.ask(incident)
    proposal = env.chat.sent()[-1]
    await env.notifier.incident_finished(incident.id)
    await env.notifier.drain()

    cleared = [body["message_id"] for name, body in env.chat.calls if name.startswith("edit")]
    assert cleared == [proposal["id"]]


def test_every_notify_template_loads_with_its_placeholders():
    for path in prompts.all_prompt_files():
        if "notify" in path.parts:
            assert prompts.placeholders(path.read_text(encoding="utf-8")) is not None


# --- the dashboard address ---


def settings(**extra) -> Settings:
    return Settings(
        data_dir=".", admin_password=SecretStr("x" * 12), secret=SecretStr("s"),
        TELEGRAM_BOT_TOKEN=SecretStr("7001:token"), TELEGRAM_CHAT_ID=CHAT, **extra,
    )  # fmt: skip


@pytest.mark.parametrize("url", ["ftp://bleep.example", "bleep.example", "javascript:alert(1)"])
def test_the_dashboard_address_must_be_an_http_url(url):
    with pytest.raises(ValidationError):
        settings(dashboard_url=url)


async def test_the_dashboard_address_reaches_the_notifier_and_is_optional(env):
    linked = setup(
        settings(dashboard_url="http://192.168.1.5:3000/"), env.db, env.bus, env.approvals
    )
    unlinked = setup(settings(), env.db, env.bus, env.approvals)
    try:
        assert linked.notifier.dashboard_url == "http://192.168.1.5:3000/"
        assert unlinked.notifier.dashboard_url is None
    finally:
        await linked.close()
        await unlinked.close()


@pytest.mark.parametrize(
    ("subject", "kind", "detail", "seen"),
    [
        (HOST, "disk", {}, "M1 is running out of disk space\\."),
        (HOST, "load", {}, "M1 is under heavy load\\."),
        (SHOP, "url_failed", {"url": "https://shop.example/health"}, '"https://shop\\.example'),
        (SHOP, "oom", {}, "shop\\-web was killed: out of memory\\."),
    ],
)
async def test_the_opener_says_what_was_seen(env, subject, kind, detail, seen):
    signal = Signal(subject=subject, kind=kind, at=env.now, detail=detail, container="shop-web")
    incident, _ = await env.repo.open_or_attach(subject, [signal])
    await env.notifier.incident_opened(incident)

    [opener] = env.chat.sent()
    assert opener["text"].split("\n")[1].startswith(seen)
