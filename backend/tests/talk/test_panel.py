"""The dashboard's chat panel (#391): the same reading and the same actions as Telegram, its own
turns and offers. A message never changes anything; only a press of an offer's Yes runs it,
once, within ten minutes and while its incident is open."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from bleep import prompts
from bleep.incidents.approvals import ApprovalStore
from bleep.incidents.bus import EventBus
from bleep.incidents.quiet import QuietStore
from bleep.incidents.repository import IncidentRepo
from bleep.schemas.api import HostView, QuietView, ServiceView
from bleep.schemas.graph import Decision, PlanStep, Proposal
from bleep.schemas.incidents import Signal, Subject
from bleep.schemas.talk import TalkLink
from bleep.store.db import Database
from bleep.talk.actions import Actions
from bleep.talk.confirms import CONFIRM_TTL
from bleep.talk.conversation import Talk
from bleep.talk.panel import WAITING_MAX, Panel
from bleep.talk.state import BleepState
from fakes import FakeChatModel

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
SHOP = "demo/compose:shop"
PLAN = Proposal(
    kind="act",
    steps=[PlanStep(action="docker.restart", host_id="demo", target="shop-web-1")],
    reason="it exited (code 137).",
)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


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


class FakeRunner:
    def __init__(self) -> None:
        self.resumed: list[tuple[str, Decision, str]] = []

    async def resume(self, incident_id: str, decision: Decision, approval_id: str) -> None:
        self.resumed.append((incident_id, decision, approval_id))


class FakeExpectations:
    def __init__(self) -> None:
        self.marked: list[tuple[str, str]] = []

    async def offered(self, incident_id: str) -> object:
        return object()

    async def expected(self, incident_id: str, via: str) -> SimpleNamespace:
        self.marked.append((incident_id, via))
        return SimpleNamespace(done=True, text="You said this is expected.")


def reading(**args) -> AIMessage:
    return AIMessage("", tool_calls=[{"name": "TalkReading", "args": args, "id": "1"}])


def said(name: str, **values: str) -> str:
    return prompts.load(f"notify/{name}", **values).strip()


@pytest.fixture
async def env(tmp_path) -> SimpleNamespace:
    e = SimpleNamespace()
    e.db = Database(tmp_path / "bleep.sqlite")
    await e.db.migrate()
    e.clock = Clock()
    e.repo, e.quiet = IncidentRepo(e.db), QuietStore(e.db, clock=e.clock)
    e.approvals, e.bus = ApprovalStore(e.db, "s"), EventBus(e.db)
    e.model = FakeChatModel(script=[])
    e.runner, e.expectations = FakeRunner(), FakeExpectations()
    state = BleepState(Health(e.quiet), e.repo, e.approvals, e.bus, clock=e.clock)

    async def no_sleep(_: float) -> None:
        return None

    e.talk = Talk(state, lambda role: e.model, sleep=no_sleep, clock=e.clock)
    e.panel = Panel(e.talk, clock=e.clock)
    e.actions = Actions(
        e.approvals, e.runner, "dashboard", e.clock, incidents=e.repo, quiet=e.quiet,
        expectations=e.expectations,
    )  # fmt: skip
    subject = Subject(kind="service", host_id="demo", service_id=SHOP)
    e.incident, _ = await e.repo.open_or_attach(
        subject, [Signal(subject=subject, kind="container_died", at=T0)]
    )
    e.approval = await e.approvals.request(e.incident.id, PLAN, [])
    return e


async def say(env, text: str, *replies: AIMessage, incident_id: str | None = None):
    env.model.script.extend(replies)
    return await env.panel.say(text, incident_id, env.actions)


async def nothing_changed(env) -> None:
    assert await env.quiet.active() == []
    assert env.runner.resumed == [] and env.expectations.marked == []
    current = await env.approvals.current(env.incident.id)
    assert current is not None and current.decision is None


async def test_a_question_is_answered_with_links_to_the_pages_it_names(env):
    answer = "shop is down; shop #1 waits for your yes. shopping is fine."
    reply = await say(env, "how is shop?", reading(kind="question", answer=answer))

    assert reply.answer == answer and reply.confirm is None
    assert reply.links == [
        TalkLink(kind="service", id=SHOP, label="shop"),
        TalkLink(kind="incident", id=env.incident.id, label="shop #1"),
    ]
    await nothing_changed(env)


ASKED = {
    "snooze": {"kind": "snooze", "subject": "shop", "minutes": 120},
    "leave_alone": {"kind": "leave_alone", "subject": "shop"},
    "expected": {"kind": "expected", "incident": "shop #1"},
    "instruct": {"kind": "instruct", "incident": "shop #1"},
}


@pytest.mark.parametrize("kind", ASKED)
async def test_a_message_alone_never_changes_anything(env, kind):
    reply = await say(env, "do it", reading(kind="action", action=ASKED[kind]))

    assert reply.confirm is not None
    assert reply.confirm.yes_label == ("Send" if kind == "instruct" else "Yes")
    assert reply.confirm.no_label == "No"
    assert reply.confirm.expires_at == T0 + CONFIRM_TTL
    await nothing_changed(env)


async def test_yes_to_a_snooze_snoozes_shop_once(env):
    reply = await say(env, "leave shop alone", reading(kind="action", action=ASKED["snooze"]))
    assert reply.confirm is not None and reply.confirm.incident_id is None
    assert reply.links == [TalkLink(kind="service", id=SHOP, label="shop")]

    done = await env.panel.press(reply.confirm.id, True, env.actions)
    again = await env.panel.press(reply.confirm.id, True, env.actions)

    [quiet] = await env.quiet.active()
    assert (quiet.target_id, quiet.mode, quiet.until) == (SHOP, "snooze", T0 + timedelta(hours=2))
    assert done.line == said("answer_on_it", until="14:00 UTC")
    assert again.line == said("answer_confirm_used")


async def test_no_drops_the_offer_and_a_later_yes_runs_nothing(env):
    reply = await say(env, "leave shop alone", reading(kind="action", action=ASKED["snooze"]))
    assert reply.confirm is not None

    declined = await env.panel.press(reply.confirm.id, False, env.actions)
    later = await env.panel.press(reply.confirm.id, True, env.actions)

    assert (declined.line, later.line) == (said("answer_confirm_no"), said("answer_confirm_used"))
    await nothing_changed(env)


async def test_an_expired_or_unknown_offer_runs_nothing(env):
    reply = await say(env, "leave shop alone", reading(kind="action", action=ASKED["leave_alone"]))
    assert reply.confirm is not None
    env.clock.now = T0 + CONFIRM_TTL

    expired = await env.panel.press(reply.confirm.id, True, env.actions)
    unknown = await env.panel.press("made-up", True, env.actions)

    assert expired.line == unknown.line == said("answer_confirm_expired")
    await nothing_changed(env)


async def test_an_offer_about_an_incident_that_ended_runs_nothing(env):
    reply = await say(env, "it's expected", reading(kind="action", action=ASKED["expected"]))
    assert reply.confirm is not None and reply.confirm.incident_id == env.incident.id
    await env.repo.set_status(env.incident.id, "resolved")

    result = await env.panel.press(reply.confirm.id, True, env.actions)

    assert result.line == said("answer_confirm_ended")
    assert env.expectations.marked == []


async def test_yes_to_expected_marks_it_from_the_dashboard(env):
    reply = await say(env, "it's expected", reading(kind="action", action=ASKED["expected"]))
    assert reply.confirm is not None

    result = await env.panel.press(reply.confirm.id, True, env.actions)

    assert env.expectations.marked == [(env.incident.id, "dashboard")]
    assert result.line == "You said this is expected."


async def test_send_gives_the_words_to_the_waiting_plan_as_the_dashboard(env):
    text = "dont restart, I'll fix the batch size"
    reply = await say(
        env, text, reading(kind="unclear"), incident_id=env.incident.id
    )  # a message about an incident whose plan waits may be an instruction
    assert reply.confirm is not None and reply.confirm.yes_label == "Send"

    result = await env.panel.press(reply.confirm.id, True, env.actions)

    [(incident_id, decision, approval_id)] = env.runner.resumed
    assert (incident_id, approval_id) == (env.incident.id, env.approval.id)
    assert (decision.kind, decision.text, decision.via) == ("instruct", text, "dashboard")
    assert result.line == said("answer_instructed")


async def test_a_plan_decided_meanwhile_takes_no_instruction(env):
    reply = await say(env, "skip it", reading(kind="unclear"), incident_id=env.incident.id)
    assert reply.confirm is not None
    decision = Decision(kind="approve", via="telegram", at=T0)
    await env.approvals.resolve(env.incident.id, decision, env.approval.id)

    result = await env.panel.press(reply.confirm.id, True, env.actions)

    assert result.line == said("answer_already_decided", channel="Telegram")
    assert env.runner.resumed == []


async def test_yes_to_a_waiting_plan_points_to_its_page_without_buttons(env):
    action = {"kind": "show_plan", "incident": "shop #1"}
    reply = await say(env, "yes, do it", reading(kind="action", action=action))

    assert reply.confirm is None
    assert reply.answer == prompts.load("talk/plan_on_page", incident="shop #1").strip()
    assert reply.links == [TalkLink(kind="incident", id=env.incident.id, label="shop #1")]
    await nothing_changed(env)


async def test_expected_where_it_does_not_apply_is_said_without_buttons(env):
    async def never(incident_id: str) -> None:
        return None

    env.expectations.offered = never
    reply = await say(env, "it's expected", reading(kind="action", action=ASKED["expected"]))

    assert reply.confirm is None
    assert reply.answer == said("answer_cannot_expect", incident="shop #1")


async def test_the_panel_keeps_its_own_turns(env):
    other = Panel(env.talk, clock=env.clock)
    await say(env, "how is shop?", reading(kind="question", answer="shop is down."))
    env.model.script.append(reading(kind="question", answer="Since 12:00."))
    await other.say("since when?", None, env.actions)

    history = str(env.model.seen[1][-1].content).split('<untrusted source="conversation"')[1]
    assert "how is shop?" not in history
    assert [t.said for t in env.panel.turns.recent()] == ["how is shop?"]
    assert [t.said for t in other.turns.recent()] == ["since when?"]


async def test_too_many_messages_at_once_are_told_bleep_is_busy(env):
    env.panel._waiting = WAITING_MAX

    reply = await env.panel.say("how is shop?", None, env.actions)

    assert reply.answer == said("answer_talk_busy") and env.model.seen == []
