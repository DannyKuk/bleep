"""Free text in Bleep's Telegram chat is answered through `talk` (#401): read by a scripted model,
answered from Bleep's own state, and never a change on its own. What the person asks for comes
back as one offer with buttons, and only the press runs it (#402)."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import AIMessage
from pydantic import SecretStr
from test_telegram import (
    CHAT,
    PLAN,
    T0,
    TOKEN,
    FakeRunner,
    FakeTelegram,
    fixture,
    set_env_plan,
)

from bleep import prompts
from bleep.config import Settings
from bleep.guard.log import GuardLog
from bleep.incidents.approvals import ApprovalStore
from bleep.incidents.bus import EventBus
from bleep.incidents.looks import Looks
from bleep.incidents.quiet import QuietStore
from bleep.incidents.repository import IncidentRepo
from bleep.llm.usage import CostLedger
from bleep.notify.notifier import Notifier, NotifySetup, setup
from bleep.notify.telegram import MessageLog, OnIt, TelegramBot
from bleep.schemas.api import HostView, QuietView, ServiceView
from bleep.schemas.graph import Decision, Proposal
from bleep.schemas.incidents import Incident, Signal, Subject
from bleep.schemas.services import HostState, Service
from bleep.schemas.settings import Budgets
from bleep.schemas.talk import TalkReply
from bleep.store.db import Database
from bleep.talk.confirms import CONFIRM_TTL
from bleep.talk.conversation import Talk
from bleep.talk.log import TalkLog
from bleep.talk.state import BleepState
from fakes import FakeChatModel, ssh_host

SHOP = "demo/shop"


class NoHealth:
    async def hosts(self):
        return []

    async def services(self):
        return []


class Health:
    """One host, `demo`, with one service, shop, quiet as the quiet store says."""

    def __init__(self, quiet: QuietStore) -> None:
        self.quiet = quiet

    async def hosts(self) -> list[HostView]:
        return [
            HostView(
                id="demo", name="demo", kind="ssh", reachable=True, last_seen=None, error=None,
                basics=None, service_count=1, health="ok",
            )
        ]  # fmt: skip

    async def services(self) -> list[ServiceView]:
        state = await self.quiet.of("service", SHOP)
        return [
            ServiceView(
                id=SHOP, host_id="demo", name="shop", source="compose", health="down",
                containers=[], urls=[], open_incident_id=None,
                quiet=QuietView.of(state) if state is not None else None,
            )
        ]  # fmt: skip


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self):
        return self.now


class FakeExpectations:
    def __init__(self) -> None:
        self.applies = True
        self.marked: list[tuple[str, str]] = []

    async def offered(self, incident_id: str) -> object | None:
        return object() if self.applies else None

    async def expected(self, incident_id: str, via: str):
        self.marked.append((incident_id, via))
        return SimpleNamespace(done=True, text="You said this is expected.")


def reading(**args) -> AIMessage:
    return AIMessage("", tool_calls=[{"name": "TalkReading", "args": args, "id": "1"}])


def fixed(name: str, **values: str) -> str:
    return prompts.load(f"talk/{name}", **values).strip()


def said(name: str, **values: str) -> str:
    return prompts.load(f"notify/{name}", **values).strip()


class Env(SimpleNamespace):
    pass


@pytest.fixture
async def env(tmp_path) -> Env:
    e = Env()
    e.db = Database(tmp_path / "bleep.sqlite")
    await e.db.migrate()
    e.clock = Clock()
    e.api, e.runner = FakeTelegram(), FakeRunner()
    e.approvals, e.bus = ApprovalStore(e.db, "s"), EventBus(e.db)
    e.repo, e.quiet = IncidentRepo(e.db), QuietStore(e.db, clock=e.clock)
    e.model = FakeChatModel(script=[])
    e.guard = GuardLog(e.db)
    state = BleepState(Health(e.quiet), e.repo, e.approvals, e.bus, clock=e.clock)

    async def no_sleep(_: float) -> None:
        return None

    e.talk = Talk(state, lambda role: e.model, sleep=no_sleep, clock=e.clock)
    http = httpx.AsyncClient(transport=httpx.MockTransport(e.api))
    e.messages = MessageLog(e.db)
    e.bot = TelegramBot(
        TOKEN, CHAT, e.approvals, e.runner, http, messages=e.messages, guard_log=e.guard,
        clock=e.clock, sleep=no_sleep, talk=e.talk, on_it=OnIt(e.repo, e.quiet, e.bus),
    )  # fmt: skip
    e.expectations = e.bot.expectations = FakeExpectations()
    notifier = Notifier(e.bus, e.bot.client, CHAT, e.messages, e.approvals, incidents=e.repo)
    subject = Subject(kind="service", host_id="demo", service_id=SHOP)
    incident, _ = await e.repo.open_or_attach(
        subject, [Signal(subject=subject, kind="container_died", at=T0)]
    )
    e.incident = incident
    request = await e.approvals.request(incident.id, PLAN, [])
    e.approval_id = request.id
    await notifier.proposal(incident, None, PLAN, request)  # message 311, as the fixtures say
    e.api.calls.clear()
    e.api.next_id = 400  # every later message gets an id of its own
    return e


async def say(env: Env, text: str, reply_to: int | None = None, *replies: AIMessage) -> list[dict]:
    """Send `text` (as a reply to `reply_to`) with the model answering `replies`; what the bot
    sent back."""
    env.model.script.extend(replies)
    name = "reply_instruction" if reply_to is not None else "plain_message"
    update = fixture(name)
    message = update["result"][0]["message"]
    message["text"] = text
    if reply_to is not None:
        message["reply_to_message"]["message_id"] = reply_to
    env.api.updates.append(update)
    await env.bot.poll_once()
    await env.bot.settle()
    return env.api.sent("sendMessage")


def offer_of(message: dict) -> tuple[str, str]:
    """(the yes label, the offer's id) of a message that carries an offer."""
    [[yes, no]] = message["reply_markup"]["inline_keyboard"]
    verb, _, confirm_id = yes["callback_data"].partition(":")
    assert (verb, no["callback_data"]) == ("yes", f"no:{confirm_id}")
    assert no["text"] == "No"
    return yes["text"], confirm_id


async def press(env: Env, confirm_id: str, verb: str = "yes", message_id: int | None = None):
    """Press an offer's button; the pop-up the bot answered with."""
    shown = env.bot._shown.get(confirm_id, 401)
    update = fixture(
        "callback_confirm",
        MESSAGE_ID=str(message_id or shown),
        CONFIRM_ID=confirm_id,
        VERB=verb,
    )
    env.api.updates.append(update)
    before = len(env.api.sent("answerCallbackQuery"))
    await env.bot.poll_once()
    pop_ups = env.api.sent("answerCallbackQuery")
    assert len(pop_ups) == before + 1
    return pop_ups[-1]["text"]


async def state_of(env: Env) -> tuple:
    """Everything a message could change: decisions, the plan, quiet, the incident."""
    record = await env.approvals.current(env.incident.id)
    incident = await env.repo.get(env.incident.id)
    quiet = [(q.scope, q.target_id, q.mode, q.until) for q in await env.quiet.active()]
    return (
        list(env.runner.resumed),
        (record.id, record.decision, record.closed) if record else None,
        quiet,
        incident.status if incident else None,
        list(env.expectations.marked),
    )


async def nothing_changed(env: Env) -> None:
    assert env.runner.resumed == [] and env.expectations.marked == []
    record = await env.approvals.current(env.incident.id)
    assert record is not None and record.decision is None and not record.closed
    assert await env.quiet.active() == []
    incident = await env.repo.get(env.incident.id)
    assert incident is not None and incident.status == env.incident.status


# --- answers (#401) ---


async def test_a_status_question_is_answered_as_a_plain_reply(env):
    [sent] = await say(
        env, "how do the services look?", None, reading(kind="question", answer="All fine.")
    )

    assert sent["text"] == "All fine."
    assert sent["reply_parameters"]["message_id"] == 321
    assert "parse_mode" not in sent and "reply_markup" not in sent  # plain text, no buttons
    await nothing_changed(env)


async def test_a_question_sent_as_a_reply_to_a_waiting_plan_is_answered_not_sent_to_it(env):
    [sent] = await say(
        env, "how long has it been down?", 311, reading(kind="question", answer="Since 12:00.")
    )

    assert sent["text"] == "Since 12:00." and "reply_markup" not in sent
    await nothing_changed(env)
    incident = env.model.seen[0][-1].content
    assert '<untrusted source="incident"' in incident and env.incident.id in incident


async def test_a_message_bleep_could_not_read_gets_the_failure_line_and_changes_nothing(env):
    [sent] = await say(env, "how is shop?", None)  # the scripted model has no answer: it fails

    assert sent["text"] == fixed("failed")
    await nothing_changed(env)


async def test_an_unclear_message_gets_the_fixed_line(env):
    [sent] = await say(env, "hmm", None, reading(kind="unclear"))
    assert sent["text"] == fixed("unclear")
    await nothing_changed(env)


async def test_a_message_over_the_cap_is_refused_without_a_model_call(env):
    [sent] = await say(env, "x" * 1001, None)

    assert "too long" in sent["text"]
    assert env.model.seen == []


async def test_the_last_turns_go_with_the_next_message(env):
    await say(env, "how is shop?", None, reading(kind="question", answer="shop is down."))
    await say(env, "since when?", None, reading(kind="question", answer="Since 12:00."))

    history = str(env.model.seen[1][-1].content).split('<untrusted source="conversation"')[1]
    assert "how is shop?" in history and "shop is down." in history


async def test_a_text_from_another_chat_never_reaches_the_model(env):
    env.api.updates.append(fixture("foreign_message"))
    await env.bot.poll_once()
    await env.bot.settle()

    assert env.model.seen == [] and env.api.sent("sendMessage") == []


# --- every action is an offer, and only the press runs it (#402) ---

KINDS = {
    "snooze": (
        "leave shop alone for 2 hours",
        {"kind": "snooze", "subject": "shop", "minutes": 120},
    ),
    "snooze_until": (
        "lass shop bis 16 Uhr in Ruhe",
        {"kind": "snooze", "subject": "shop", "until": "2026-11-02 16:00"},
    ),
    "leave_alone": ("leave shop alone", {"kind": "leave_alone", "subject": "shop"}),
    "watch_again": ("watch shop again", {"kind": "watch_again", "subject": "shop"}),
    "expected": ("that's expected", {"kind": "expected", "incident": "shop #1"}),
    "instruct": ("tell shop #1's plan: no restart", {"kind": "instruct", "incident": "shop #1"}),
}
LINES = {
    "snooze": ("Yes", fixed("confirm_snooze", subject="shop", until="14:00 UTC")),
    "snooze_until": ("Yes", fixed("confirm_snooze", subject="shop", until="16:00 UTC")),
    "leave_alone": ("Yes", fixed("confirm_leave_alone", subject="shop")),
    "watch_again": ("Yes", fixed("confirm_watch_again", subject="shop")),
    "expected": ("Yes", fixed("confirm_expected", incident="shop #1")),
    "instruct": ("Send", fixed("confirm_instruct", incident="shop #1")),
}


async def offered(env: Env, kind: str, reply_to: int | None = None) -> str:
    """Ask for the action `kind` in a message; the offer's id."""
    if kind == "watch_again":
        await env.quiet.set("service", SHOP, "leave_alone", None)
    text, action = KINDS[kind]
    [sent] = await say(env, text, reply_to, reading(kind="action", action=action))
    label, confirm_id = offer_of(sent)
    assert (label, sent["text"]) == LINES[kind]
    assert "parse_mode" not in sent  # plain text, built by code from the checked fields
    return confirm_id


@pytest.mark.parametrize("kind", [*KINDS, "show_plan"])
async def test_a_message_alone_never_changes_anything_for_any_kind(env, kind):
    if kind == "show_plan":
        action = {"kind": "show_plan", "incident": "shop #1"}
        [sent] = await say(env, "yes, do it", None, reading(kind="action", action=action))
        assert "approve:" in str(sent["reply_markup"])  # the plan's own buttons, not pressed
    else:
        await offered(env, kind)
    if kind == "watch_again":
        [quiet] = await env.quiet.active()
        assert quiet.mode == "leave_alone"  # still left alone: only the press ends it
        await env.quiet.clear("service", SHOP)
    await nothing_changed(env)


async def test_yes_to_a_snooze_snoozes_the_service_until_the_time_shown(env):
    confirm_id = await offered(env, "snooze")

    pop_up = await press(env, confirm_id)

    [quiet] = await env.quiet.active()
    assert (quiet.scope, quiet.target_id, quiet.mode) == ("service", SHOP, "snooze")
    assert quiet.until == T0 + timedelta(hours=2)
    assert pop_up == said("answer_on_it", until="14:00 UTC")
    [cleared] = env.api.sent("editMessageReplyMarkup")
    assert cleared["reply_markup"] == {"inline_keyboard": []}


async def test_yes_to_a_snooze_until_a_time_snoozes_until_that_time(env):
    await press(env, await offered(env, "snooze_until"))

    [quiet] = await env.quiet.active()
    assert quiet.until == T0 + timedelta(hours=4)


async def test_yes_to_leave_alone_leaves_it_alone_with_no_end(env):
    pop_up = await press(env, await offered(env, "leave_alone"))

    [quiet] = await env.quiet.active()
    assert (quiet.mode, quiet.until) == ("leave_alone", None)
    assert pop_up == said("answer_left_alone", subject="shop")


async def test_yes_to_watch_again_ends_the_quiet(env):
    pop_up = await press(env, await offered(env, "watch_again"))

    assert await env.quiet.active() == []
    assert pop_up == said("answer_watching", subject="shop")


async def test_yes_to_expected_goes_through_its_expectations(env):
    pop_up = await press(env, await offered(env, "expected"))

    assert env.expectations.marked == [(env.incident.id, "telegram")]
    assert pop_up == "You said this is expected."


async def test_send_gives_the_persons_words_to_the_plan_that_waits(env):
    pop_up = await press(env, await offered(env, "instruct"))

    [(incident_id, decision)] = env.runner.resumed
    assert (incident_id, decision.kind) == (env.incident.id, "instruct")
    assert (decision.text, decision.via) == ("tell shop #1's plan: no restart", "telegram")
    assert env.runner.approval_ids == [env.approval_id]
    assert pop_up == said("answer_instructed")


async def test_yes_to_a_waiting_plan_shows_its_own_buttons_again(env):
    action = {"kind": "show_plan", "incident": "shop #1"}
    [sent] = await say(env, "yes, do it", None, reading(kind="action", action=action))

    [[approve, reject]] = sent["reply_markup"]["inline_keyboard"]
    assert approve == {"text": "Approve", "callback_data": f"approve:{env.approval_id}"}
    assert reject == {"text": "Reject", "callback_data": f"reject:{env.approval_id}"}
    assert "docker.restart shop" in sent["text"].replace("\\", "")
    await nothing_changed(env)

    env.api.updates.append(fixture("callback_approve", APPROVAL_ID=env.approval_id))
    await env.bot.poll_once()  # the press, on the message the fixture names, approves
    assert [d.kind for _, d in env.runner.resumed] == ["approve"]


async def replace_plan(env: Env, plan: Proposal) -> str:
    await env.approvals.resolve(
        env.incident.id, Decision(kind="instruct", text="x", via="dashboard", at=T0)
    )
    return (await env.approvals.request(env.incident.id, plan, [])).id


async def test_a_yes_to_a_refused_plan_is_told_it_cannot_be_approved(env):
    await replace_plan(env, PLAN.model_copy(update={"refused": True}))
    action = {"kind": "show_plan", "incident": "shop #1"}

    [sent] = await say(env, "go ahead", None, reading(kind="action", action=action))

    assert sent["text"] == said("answer_refused_plan") and "reply_markup" not in sent


async def test_a_yes_to_a_plan_telegram_cannot_show_in_full_shows_reject_only(env):
    plan_id = await replace_plan(env, set_env_plan("postgres://shop@db/" + "x" * 900))
    action = {"kind": "show_plan", "incident": "shop #1"}

    [sent] = await say(env, "go ahead", None, reading(kind="action", action=action))

    [[reject]] = sent["reply_markup"]["inline_keyboard"]
    assert reject == {"text": "Reject", "callback_data": f"reject:{plan_id}"}
    await nothing_changed(env)


async def test_a_yes_to_a_plan_with_no_message_in_the_chat_never_opens_its_thread(env):
    other = Subject(kind="service", host_id="demo", service_id="demo/web")
    incident, _ = await env.repo.open_or_attach(
        other, [Signal(subject=other, kind="container_died", at=T0)]
    )
    await env.approvals.request(incident.id, PLAN, [])  # its plan never reached Telegram
    action = {"kind": "show_plan", "incident": incident.short_name}

    [sent] = await say(env, "go ahead", None, reading(kind="action", action=action))

    assert sent["text"] == said("answer_plan_on_dashboard") and "reply_markup" not in sent
    assert await env.messages.first_for(incident.id) is None  # its opener is still to come


# --- presses that change nothing ---


async def test_no_drops_the_offer_and_changes_nothing(env):
    confirm_id = await offered(env, "snooze")

    pop_up = await press(env, confirm_id, "no")

    assert pop_up == said("answer_confirm_no")
    await nothing_changed(env)
    assert await press(env, confirm_id) == said("answer_confirm_used")  # a yes after it
    await nothing_changed(env)


async def test_a_second_press_runs_nothing_again(env):
    confirm_id = await offered(env, "instruct")
    await press(env, confirm_id)
    before = await state_of(env)

    pop_up = await press(env, confirm_id)

    assert pop_up == said("answer_confirm_used")
    assert await state_of(env) == before
    assert len(env.runner.resumed) == 1


async def test_an_offer_expires_after_ten_minutes_and_its_buttons_go(env):
    confirm_id = await offered(env, "snooze")
    message_id = env.bot._shown[confirm_id]
    env.clock.now += CONFIRM_TTL

    await env.bot.poll_once()  # each poll sweeps the offers that expired

    [cleared] = env.api.sent("editMessageReplyMarkup")
    assert cleared["message_id"] == message_id
    assert await press(env, confirm_id, message_id=message_id) == said("answer_confirm_expired")
    await nothing_changed(env)


async def test_an_unknown_offer_is_answered_and_runs_nothing(env):
    pop_up = await press(env, "never-given", message_id=401)

    assert pop_up == said("answer_confirm_expired")
    await nothing_changed(env)


async def test_a_press_on_another_message_than_the_offers_runs_nothing(env):
    confirm_id = await offered(env, "snooze")

    pop_up = await press(env, confirm_id, message_id=999)

    assert pop_up == said("answer_confirm_expired")
    await nothing_changed(env)
    await press(env, confirm_id)  # its own message still works
    assert len(await env.quiet.active()) == 1


async def test_a_press_from_another_chat_is_dropped_and_logged(env):
    confirm_id = await offered(env, "snooze")
    env.api.calls.clear()
    env.api.updates.append(
        fixture("foreign_callback_confirm", MESSAGE_ID="401", CONFIRM_ID=confirm_id)
    )

    await env.bot.poll_once()

    assert [method for method, _ in env.api.calls] == ["getUpdates"]  # nothing answered
    await nothing_changed(env)
    entries = await env.guard.entries("telegram")
    assert [e.kind for e in entries] == ["foreign_chat"]
    await press(env, confirm_id)  # the offer still waits for its own chat
    assert len(await env.quiet.active()) == 1


async def test_send_after_the_plan_was_decided_elsewhere_wins_nothing(env):
    confirm_id = await offered(env, "instruct")
    await env.approvals.resolve(
        env.incident.id, Decision(kind="reject", via="dashboard", at=T0), env.approval_id
    )

    pop_up = await press(env, confirm_id)

    assert pop_up == "Already decided on the dashboard"
    assert env.runner.resumed == []


async def test_an_offer_about_an_incident_ends_with_it(env):
    confirm_id = await offered(env, "expected")
    message_id = env.bot._shown[confirm_id]
    assert message_id in await env.messages.ids_for_incident(env.incident.id)  # cleared with it
    await env.repo.close_unfinished(env.incident.id)

    pop_up = await press(env, confirm_id)

    assert pop_up == said("answer_confirm_ended")
    assert env.expectations.marked == []


async def test_an_instruction_offer_is_cleared_when_its_plan_is_decided(env):
    confirm_id = await offered(env, "instruct")
    assert env.bot._shown[confirm_id] in await env.messages.ids_for_approval(env.approval_id)


async def test_expected_where_nothing_can_be_expected_is_not_offered(env):
    env.expectations.applies = False
    text, action = KINDS["expected"]

    [sent] = await say(env, text, None, reading(kind="action", action=action))

    assert sent["text"] == said("answer_cannot_expect", incident="shop #1")
    assert "reply_markup" not in sent


async def test_a_snooze_of_a_service_bleep_does_not_know_is_not_offered(env):
    action = {"kind": "snooze", "subject": "shoop", "minutes": 60}
    [sent] = await say(env, "leave shoop alone", None, reading(kind="action", action=action))

    assert sent["text"] == fixed("no_subject") and "reply_markup" not in sent


# --- a reply to a waiting plan is confirmed like every other change ---


async def test_an_instruction_as_a_reply_to_a_waiting_plan_is_only_offered(env):
    text = "dont restart, ill fix the batch size myself"
    [sent] = await say(env, text, 311, reading(kind="action", action={"kind": "instruct"}))

    label, confirm_id = offer_of(sent)
    assert (label, sent["text"]) == LINES["instruct"]
    await nothing_changed(env)

    await press(env, confirm_id)
    [(_, decision)] = env.runner.resumed
    assert (decision.kind, decision.text) == ("instruct", text)


@pytest.mark.parametrize(
    "reading_of",
    [
        {"kind": "refusal", "refusal": "change"},
        {"kind": "unclear"},
        None,  # Bleep could not read it
    ],
)
async def test_a_reply_to_a_waiting_plan_that_is_no_question_is_offered_to_it(env, reading_of):
    replies = [reading(**reading_of)] if reading_of else []
    [sent] = await say(env, "restart the db first, then shop", 311, *replies)

    label, _ = offer_of(sent)
    assert label == "Send" and sent["text"].endswith(LINES["instruct"][1])
    await nothing_changed(env)


async def test_a_reply_to_a_replaced_plan_offers_nothing(env):
    await env.approvals.resolve(
        env.incident.id, Decision(kind="instruct", text="x", via="dashboard", at=T0)
    )
    await env.approvals.request(env.incident.id, PLAN, [])

    [sent] = await say(env, "do it differently", 311, reading(kind="unclear"))

    assert "reply_markup" not in sent


async def test_a_reply_to_an_incident_with_no_plan_waiting_is_answered_not_decided(env):
    await env.approvals.resolve(
        env.incident.id, Decision(kind="reject", via="dashboard", at=T0), env.approval_id
    )
    await env.messages.add(312, env.incident.id, "", T0)  # the opener: it showed no plan

    [sent] = await say(
        env, "do this instead", 312, reading(kind="action", action={"kind": "instruct"})
    )

    assert sent["text"] == fixed("no_plan", incident="shop #1")
    assert env.runner.resumed == []


async def test_an_approve_pressed_right_after_a_reply_wins_and_the_reply_offers_nothing(env):
    reply = fixture("reply_instruction")
    press_approve = fixture("callback_approve", APPROVAL_ID=env.approval_id)
    env.model.script.append(reading(kind="action", action={"kind": "instruct"}))
    env.api.updates.append({"ok": True, "result": reply["result"] + press_approve["result"]})

    await env.bot.poll_once()
    await env.bot.settle()

    assert [d.kind for _, d in env.runner.resumed] == ["approve"]
    [answer] = env.api.sent("sendMessage")
    # The reply is read in the background, before or after the press is decided: either way it
    # learns the plan no longer waits, and offers nothing.
    no_plan, decided = fixed("no_plan", incident="shop #1"), "Already decided on Telegram"
    assert answer["text"] in (no_plan, decided) and "reply_markup" not in answer


async def test_send_after_an_approve_on_the_same_plan_wins_nothing(env):
    confirm_id = await offered(env, "instruct")
    env.api.updates.append(fixture("callback_approve", APPROVAL_ID=env.approval_id))
    await env.bot.poll_once()

    pop_up = await press(env, confirm_id)

    assert [d.kind for _, d in env.runner.resumed] == ["approve"]
    assert pop_up == "Already decided on Telegram"


# --- answering in the background ---


class SlowTalk:
    """A conversation whose model has not answered yet."""

    def __init__(self) -> None:
        self.go = asyncio.Event()
        self.asked: list[str] = []
        self.log = TalkLog()

    async def reply(self, text, incident_id=None, turns=(), *, surface):
        self.asked.append(text)
        await self.go.wait()
        return TalkReply(kind="question", answer="late answer")


async def test_a_slow_answer_never_holds_a_button_press_behind_it(env):
    env.bot.talk = slow = SlowTalk()
    env.api.updates.append(fixture("reply_instruction"))  # a reply to the plan, too
    await env.bot.poll_once()
    await asyncio.sleep(0)
    assert slow.asked == ["Restart the database first, then the web container"]

    env.api.updates.append(fixture("callback_approve", APPROVAL_ID=env.approval_id))
    await env.bot.poll_once()

    assert [d.kind for _, d in env.runner.resumed] == ["approve"]  # decided while it thinks
    slow.go.set()
    await env.bot.settle()
    assert env.api.sent("sendMessage")[-1]["text"] == "late answer"


async def test_more_messages_than_bleep_answers_at_once_get_the_busy_line(env):
    env.bot.talk = slow = SlowTalk()
    for n in range(4):
        update = fixture("plain_message")
        update["result"][0]["message"]["message_id"] = 400 + n
        update["result"][0]["message"]["text"] = f"question {n}"
        env.api.updates.append(update)
        await env.bot.poll_once()
        await asyncio.sleep(0)

    [busy] = env.api.sent("sendMessage")
    assert busy["text"] == prompts.load("notify/answer_talk_busy").strip()
    assert busy["reply_parameters"]["message_id"] == 403
    slow.go.set()
    await env.bot.settle()
    assert slow.asked == ["question 0", "question 1", "question 2"]


async def test_stopping_the_setup_ends_the_poller_and_cuts_off_answers(env):
    env.bot.talk = SlowTalk()
    env.api.updates.append(fixture("plain_message"))
    await env.bot.poll_once()
    built = NotifySetup(Notifier(env.bus), env.bot)
    built.start(env.runner)

    await built.stop()

    assert built._task is None and not env.bot._talking


async def test_stopping_cuts_off_an_answer_in_flight(env):
    env.bot.talk = slow = SlowTalk()
    env.api.updates.append(fixture("plain_message"))
    await env.bot.poll_once()
    await asyncio.sleep(0)

    await env.bot.stop_talking()

    assert slow.asked == ["hello"] and env.api.sent("sendMessage") == []
    assert not env.bot._talking


async def test_setup_gives_the_bot_the_conversation(tmp_path):
    db = Database(tmp_path / "bleep.sqlite")
    settings = Settings(
        data_dir=tmp_path, admin_password=SecretStr("x" * 12), secret=SecretStr("s"),
        TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_CHAT_ID=CHAT,
    )  # fmt: skip
    state = BleepState(NoHealth(), IncidentRepo(db), ApprovalStore(db, "s"), EventBus(db))
    talk = Talk(state, lambda role: FakeChatModel(script=[]))

    built = setup(settings, db, EventBus(db), ApprovalStore(db, "s"), talk=talk)

    assert built.bot is not None and built.bot.talk is talk
    await built.close()


# --- a look on request (#403) ---


class LookRegistry:
    """The host `demo` and its one service, shop, as the registry lists them."""

    def __init__(self) -> None:
        self.reachable = True

    def service(self, service_id: str) -> Service | None:
        if service_id != SHOP:
            return None
        return Service(id=SHOP, host_id="demo", name="shop", source="compose", containers=[])

    def hosts(self) -> list[HostState]:
        return [HostState(config=ssh_host(id="demo"), reachable=self.reachable, last_seen=None)]


class Opener:
    """The runner a look starts: it announces the incident as the real one does first."""

    def __init__(self, notifier: Notifier) -> None:
        self.notifier = notifier
        self.started: list[str] = []

    async def start(self, incident: Incident) -> None:
        self.started.append(incident.id)
        await self.notifier.incident_opened(incident)


@pytest.fixture
def looks(env) -> Opener:
    notifier = Notifier(
        env.bus, env.bot.client, CHAT, env.messages, env.approvals, incidents=env.repo
    )
    opener = Opener(notifier)
    env.registry = LookRegistry()
    env.bot.looks = Looks(env.repo, opener, env.quiet, env.registry, clock=env.clock)  # type: ignore[arg-type]
    return opener


LOOK = reading(kind="action", action={"kind": "look_again", "subject": "shop"})


async def look_offered(env: Env) -> str:
    [sent] = await say(env, "look at shop again", None, LOOK)
    label, confirm_id = offer_of(sent)
    assert (label, sent["text"]) == ("Yes", fixed("confirm_look_again", subject="shop"))
    env.api.calls.clear()
    return confirm_id


async def test_a_look_message_alone_opens_nothing_and_the_tap_opens_one_incident(env, looks):
    await env.repo.set_status(env.incident.id, "closed")
    confirm_id = await look_offered(env)
    assert looks.started == [] and len(await env.repo.list()) == 1  # the message opened none

    pop_up = await press(env, confirm_id)

    [incident] = [i for i in await env.repo.list() if i.id != env.incident.id]
    assert (incident.short_name, incident.status) == ("shop #2", "open")
    assert [s.kind for s in await env.repo.signals(incident.id)] == ["look_requested"]
    assert looks.started == [incident.id]
    assert pop_up == fixed("look_started", subject="shop", incident="shop #2")
    [opener] = env.api.sent("sendMessage")
    text = opener["text"].replace("\\", "")
    assert text.startswith("*🔴 New: shop #2 · demo*")  # new: asked for, not shop #1 again
    assert prompts.load("notify/seen/look_requested", service="shop").strip() in text
    assert "reply_parameters" not in opener


async def test_a_second_or_an_expired_press_of_a_look_opens_nothing(env, looks):
    await env.repo.set_status(env.incident.id, "closed")
    confirm_id = await look_offered(env)
    await press(env, confirm_id)
    env.api.calls.clear()  # the press sent shop #2's opener
    stale = await look_offered(env)  # shop #2 is open now, but the snapshot here does not say
    env.clock.now += CONFIRM_TTL
    await env.repo.set_status(looks.started[0], "closed")

    assert await press(env, confirm_id) == said("answer_confirm_used")
    assert await press(env, stale) == said("answer_confirm_expired")
    assert len(looks.started) == 1 and len(await env.repo.list()) == 2


async def test_a_look_pressed_while_an_incident_is_open_names_it_and_opens_nothing(env, looks):
    confirm_id = await look_offered(env)  # the snapshot here does not know of shop #1

    pop_up = await press(env, confirm_id)

    assert pop_up == fixed("look_open", incident="shop #1", subject="shop")
    assert looks.started == [] and [i.id for i in await env.repo.list()] == [env.incident.id]
    assert [s.kind for s in await env.repo.signals(env.incident.id)] == ["container_died"]


async def test_a_look_pressed_once_the_service_went_quiet_opens_nothing(env, looks):
    await env.repo.set_status(env.incident.id, "closed")
    confirm_id = await look_offered(env)
    await env.quiet.set("service", SHOP, "snooze", T0 + timedelta(hours=2))

    pop_up = await press(env, confirm_id)

    assert pop_up == fixed("look_snoozed", subject="shop", until="14:00 UTC")
    assert looks.started == [] and len(await env.repo.list()) == 1


async def test_a_look_pressed_while_the_host_does_not_answer_opens_nothing(env, looks):
    await env.repo.set_status(env.incident.id, "closed")
    confirm_id = await look_offered(env)
    env.registry.reachable = False

    pop_up = await press(env, confirm_id)

    assert pop_up == fixed("look_unreachable", subject="shop", host="demo")
    assert looks.started == [] and len(await env.repo.list()) == 1


async def test_a_look_pressed_once_the_days_cap_is_spent_opens_nothing(env, looks):
    ledger = CostLedger(env.db)
    budgets = Budgets(daily_cost_usd=0.5)
    env.bot.looks = Looks(
        env.repo, looks, env.quiet, env.registry, ledger=ledger, budgets=lambda: budgets
    )  # type: ignore[arg-type]
    await env.repo.set_status(env.incident.id, "closed")
    confirm_id = await look_offered(env)  # this chat's Talk reads no ledger: the offer is made
    await ledger.add("inc_other", "m", 1000, 1000, 0.6)

    pop_up = await press(env, confirm_id)

    assert pop_up == fixed("look_spent")
    assert looks.started == [] and len(await env.repo.list()) == 1


class Unannounced:
    """A runner that starts nothing yet: the test announces the incident when it chooses."""

    async def start(self, incident: Incident) -> None:
        return None


async def opener_of(env: Env, incident: Incident) -> str:
    env.api.calls.clear()
    await looks_notifier(env).incident_opened(incident)
    [opener] = env.api.sent("sendMessage")
    return opener["text"].replace("\\", "")


def looks_notifier(env: Env) -> Notifier:
    return Notifier(env.bus, env.bot.client, CHAT, env.messages, env.approvals, incidents=env.repo)


async def test_a_watcher_signal_that_attached_first_never_changes_a_looks_opener(env):
    await env.repo.set_status(env.incident.id, "closed")
    subject = Subject(kind="service", host_id="demo", service_id=SHOP)
    looks = Looks(env.repo, Unannounced(), env.quiet, LookRegistry())  # type: ignore[arg-type]
    look = await looks.look(SHOP, "telegram")
    unhealthy = Signal(subject=subject, kind="unhealthy", at=T0, container="shop-web-1")
    await env.repo.open_or_attach(subject, [unhealthy])  # attaches between open and announce

    text = await opener_of(env, look.incident)

    assert text.startswith("*🔴 New: shop #2 · demo*")
    assert prompts.load("notify/seen/look_requested", service="shop").strip() in text
    assert "unhealthy" not in text


async def test_a_bare_look_is_no_earlier_incident_for_the_next_ones_again(env, looks):
    await env.repo.set_status(env.incident.id, "closed")
    await press(env, await look_offered(env))
    [look_id] = looks.started
    await env.repo.set_status(look_id, "closed")
    subject = Subject(kind="service", host_id="demo", service_id=SHOP)
    died = Signal(subject=subject, kind="container_died", at=T0, container="shop-web-1")
    incident, _ = await env.repo.open_or_attach(subject, [died])

    text = await opener_of(env, incident)

    assert text.startswith("*🔁 Again: shop #3 ·") and text.split("*")[1].endswith("shop #1")
    assert "shop #2" not in text


async def test_a_look_pressed_without_looks_wired_opens_nothing(env):
    await env.repo.set_status(env.incident.id, "closed")
    confirm_id = await look_offered(env)

    assert await press(env, confirm_id) == said("answer_unknown")
    assert len(await env.repo.list()) == 1


async def test_the_setup_gives_the_bot_its_looks_when_it_starts(env):
    built = NotifySetup(Notifier(env.bus), env.bot)
    sentinel = object()
    built.use_looks(sentinel)  # type: ignore[arg-type]

    built.start(env.runner)

    assert env.bot.looks is sentinel
    await built.stop()


# --- the talk log (#411) ---


def logged(env: Env):
    entries = env.talk.log.exchanges()
    assert all(e.surface == "telegram" for e in entries)
    return entries


async def test_a_question_lands_in_the_log_as_telegrams_with_the_answer_sent(env):
    await say(
        env, "how long has it been down?", 311, reading(kind="question", answer="Since 12:00.")
    )

    [entry] = logged(env)
    assert (entry.text, entry.incident_id) == ("how long has it been down?", env.incident.id)
    assert entry.reading is not None and entry.reading.kind == "question"
    assert (entry.answer, entry.links, entry.press) == ("Since 12:00.", [], None)


async def test_a_refused_an_unclear_and_a_failed_reading_land_in_the_log(env):
    await say(env, "rm -rf it", None, reading(kind="refusal", refusal="change"))
    await say(env, "hmm", None, reading(kind="unclear"))
    await say(env, "how is shop?", None)  # the scripted model has no answer: it fails

    refused, unclear, failed = logged(env)
    assert (refused.kind, refused.answer) == ("refusal", fixed("refused_change"))
    assert refused.reading is not None and refused.reading.refusal == "change"
    assert (unclear.kind, unclear.answer) == ("unclear", fixed("unclear"))
    assert (failed.kind, failed.reading, failed.answer) == ("failed", None, fixed("failed"))
    assert failed.error is not None


async def test_a_pressed_offer_lands_in_the_log_with_its_press(env):
    confirm_id = await offered(env, "snooze")

    pop_up = await press(env, confirm_id)
    await press(env, confirm_id)  # a second press ran nothing

    [entry] = logged(env)
    assert entry.offer is not None and entry.offer.target_id == SHOP
    assert entry.answer == LINES["snooze"][1]
    assert entry.press is not None
    assert (entry.press.yes, entry.press.line, entry.press.at) == (True, pop_up, T0)


async def test_a_press_on_another_message_is_not_logged_as_the_offers(env):
    confirm_id = await offered(env, "snooze")

    await press(env, confirm_id, message_id=999)
    assert logged(env)[0].press is None
    await press(env, confirm_id, verb="no")

    [entry] = logged(env)
    assert entry.press is not None
    assert (entry.press.yes, entry.press.line) == (False, said("answer_confirm_no"))


async def test_a_secret_typed_into_telegram_is_redacted_in_the_log(env):
    token = "ghp_" + "A1b2" * 9
    await say(env, f"is {token} the right token?", None, reading(kind="refusal", refusal="secret"))

    [entry] = logged(env)
    assert entry.text == "is <redacted> the right token?"
    assert token not in str(env.talk.log.exchanges())


async def test_a_press_whose_run_fails_is_logged_as_failed_by_the_errors_type(env, monkeypatch):
    confirm_id = await offered(env, "snooze")

    async def broken(self, offer):
        raise RuntimeError("database is locked at /data/bleep.db")

    monkeypatch.setattr("bleep.talk.actions.Actions.run", broken)
    env.api.updates.append(
        fixture("callback_confirm", MESSAGE_ID=str(env.bot._shown[confirm_id]),
                CONFIRM_ID=confirm_id, VERB="yes")
    )  # fmt: skip
    await env.bot.poll_once()  # the poller logs the error and goes on

    [entry] = logged(env)
    assert entry.press is not None
    assert entry.press.line == fixed("log_press_failed", error="RuntimeError")
