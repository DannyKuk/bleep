"""The dashboard's chat panel (#391): the same reading and the same actions as Telegram, its own
turns and offers. A message never changes anything; only a press of an offer's Yes runs it,
once, within ten minutes and while its incident is open."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from bleep import prompts
from bleep.incidents.approvals import ApprovalStore
from bleep.incidents.bus import EventBus
from bleep.incidents.looks import Look
from bleep.incidents.quiet import QuietStore
from bleep.incidents.repository import IncidentRepo
from bleep.schemas.api import HostView, QuietView, ServiceView
from bleep.schemas.graph import Decision, PlanStep, Proposal
from bleep.schemas.incidents import Signal, Subject
from bleep.schemas.shown import Shown
from bleep.schemas.talk import TalkLink, TalkReply
from bleep.store.db import Database
from bleep.talk.actions import Actions
from bleep.talk.confirms import CONFIRM_TTL
from bleep.talk.conversation import Talk
from bleep.talk.log import TalkLog, transcript
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
        return SimpleNamespace(done=True, said=Shown.of("expected/said", sentence="x"))


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
    assert result.line == "You said this is expected. x"


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

    assert result.line == said("answer_already_decided", channel="on Telegram")
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


class SlowTalk:
    """Answers each message only once `go` is set, so messages pile up behind the first."""

    def __init__(self) -> None:
        self.go = asyncio.Event()
        self.read: list[str] = []
        self.log = TalkLog()

    async def reply(self, text: str, incident_id: str | None, turns, *, surface) -> TalkReply:
        self.read.append(text)
        await self.go.wait()
        return TalkReply(kind="question", answer=f"about {text}")


async def test_a_message_beyond_three_at_once_is_told_bleep_is_busy(env):
    talk = SlowTalk()
    panel = Panel(talk, clock=env.clock)  # type: ignore[arg-type]
    first = [asyncio.create_task(panel.say(f"q{i}", None, env.actions)) for i in range(WAITING_MAX)]
    await asyncio.sleep(0)
    assert panel.waiting == WAITING_MAX and talk.read == ["q0"]  # the rest wait their turn

    busy = await panel.say("one more", None, env.actions)

    assert busy.answer == prompts.load("talk/busy").strip() and busy.confirm is None
    talk.go.set()
    answers = await asyncio.gather(*first)
    assert [a.answer for a in answers] == ["about q0", "about q1", "about q2"]
    assert panel.waiting == 0 and "one more" not in talk.read
    assert (await panel.say("again", None, env.actions)).answer == "about again"


async def test_a_yes_to_a_plan_bleeps_checks_refused_says_it_cannot_be_approved(env):
    decision = Decision(kind="instruct", text="x", via="dashboard", at=T0)
    await env.approvals.resolve(env.incident.id, decision)
    await env.approvals.request(env.incident.id, PLAN.model_copy(update={"refused": True}), [])
    action = {"kind": "show_plan", "incident": "shop #1"}

    reply = await say(env, "go ahead", reading(kind="action", action=action))

    assert reply.confirm is None
    assert reply.answer == prompts.load("talk/plan_refused", incident="shop #1").strip()
    assert reply.links == [TalkLink(kind="incident", id=env.incident.id, label="shop #1")]


async def test_a_yes_to_a_no_action_plan_bleep_could_run_says_there_is_nothing_to_approve(env):
    decision = Decision(kind="instruct", text="x", via="dashboard", at=T0)
    await env.approvals.resolve(env.incident.id, decision)
    could_run = Proposal(kind="no_action", reason="the value is yours", fix_runnable=True)
    await env.approvals.request(env.incident.id, could_run, [])
    action = {"kind": "show_plan", "incident": "shop #1"}

    reply = await say(env, "go ahead", reading(kind="action", action=action))

    assert reply.confirm is None
    assert reply.answer == prompts.load("talk/plan_no_action", incident="shop #1").strip()


class FakeLooks:
    def __init__(self, incident) -> None:
        self.incident = incident
        self.asked: list[tuple[str, str]] = []

    async def look(self, service_id: str, via: str) -> Look:
        self.asked.append((service_id, via))
        return Look("opened", service_id, "shop", "demo", incident=self.incident)


async def test_yes_to_a_look_opens_it_from_the_dashboard_and_only_then(env):
    env.actions.looks = looks = FakeLooks(env.incident)
    action = {"kind": "look_again", "subject": "shop"}
    reply = await say(env, "look at shop again", reading(kind="action", action=action))
    assert reply.confirm is not None and reply.confirm.yes_label == "Yes"
    assert looks.asked == []

    result = await env.panel.press(reply.confirm.id, True, env.actions)

    assert looks.asked == [(SHOP, "dashboard")]
    started = prompts.load("talk/look_started_here", subject="shop", incident="shop #1").strip()
    assert (result.line, result.incident_id) == (started, env.incident.id)
    [entry] = env.talk.log.exchanges()
    assert entry.press is not None and entry.press.incident_id == env.incident.id


async def test_a_press_that_is_no_look_follows_no_incident(env):
    reply = await say(env, "leave shop alone", reading(kind="action", action=ASKED["snooze"]))
    assert reply.confirm is not None

    result = await env.panel.press(reply.confirm.id, True, env.actions)

    assert result.incident_id is None


async def test_a_look_that_failed_to_start_is_logged_by_its_errors_type(env):
    class Broken:
        async def look(self, service_id: str, via: str) -> Look:
            raise RuntimeError("ssh: secret-host refused")

    env.actions.looks = Broken()
    action = {"kind": "look_again", "subject": "shop"}
    reply = await say(env, "look at shop again", reading(kind="action", action=action))
    assert reply.confirm is not None

    with pytest.raises(RuntimeError):
        await env.panel.press(reply.confirm.id, True, env.actions)

    [entry] = env.talk.log.exchanges()
    assert entry.press is not None and entry.press.incident_id is None
    assert "RuntimeError" in entry.press.line and "secret-host" not in entry.press.line


def incident_read(env, call: int) -> str:
    prompt = str(env.model.seen[call][-1].content)
    return prompt.split('<untrusted source="incident"')[1].split("</untrusted")[0]


async def test_a_question_right_after_a_look_is_read_with_what_the_look_checked(env):
    """#422: "how did you check it?" sent on its own, not on the incident's page."""
    env.actions.looks = FakeLooks(env.incident)
    env.actions.looked = env.panel.turns.about
    args = {"container": "shop-web-1", "since_minutes": 60}
    await env.bus.publish(env.incident.id, "tool_call", {"name": "container_logs", "args": args})
    await env.bus.publish(
        env.incident.id,
        "tool_result",
        {"name": "container_logs", "summary_first_line": "GET /health 200"},
    )
    action = {"kind": "look_again", "subject": "shop"}
    offer = await say(env, "look at shop", reading(kind="action", action=action))
    assert offer.confirm is not None
    env.clock.now = T0 + timedelta(minutes=1)
    await env.panel.press(offer.confirm.id, True, env.actions)

    env.clock.now = T0 + timedelta(minutes=9)
    answer = "I read the last 60 minutes of shop-web-1's logs: they answer 200."
    await say(env, "how did you check it?", reading(kind="question", answer=answer))
    env.clock.now = T0 + timedelta(minutes=30)
    await say(env, "and how is grafana?", reading(kind="question", answer="Fine."))

    checked = incident_read(env, 1)
    assert '"tool":"container_logs"' in checked and "GET /health 200" in checked
    assert '"target":"shop-web-1"' in checked and '"minutes":60' in checked
    assert [t.incident_id for t in env.panel.turns.recent()] == [
        env.incident.id,
        env.incident.id,
        None,
    ]
    assert "null" in incident_read(env, 2)  # twenty minutes on, it is about nothing


async def test_a_chat_that_goes_on_does_not_keep_the_incident_and_never_reaches_its_plan(env):
    """The window starts at the message on the incident's page and does not slide: 25 minutes of
    chat in gaps under 10 minutes, then "restart grafana" on its own is refused, not offered to
    shop #1's waiting plan."""
    question = reading(kind="question", answer="shop is down; its plan waits for your yes.")
    await say(env, "what about shop?", question, incident_id=env.incident.id)
    for minutes in (8, 16, 24):
        env.clock.now = T0 + timedelta(minutes=minutes)
        await say(env, "and now?", reading(kind="question", answer="Still the same."))
    env.clock.now = T0 + timedelta(minutes=25)

    reply = await say(env, "restart grafana", reading(kind="refusal", refusal="change"))

    assert reply.answer == prompts.load("talk/refused_change").strip() and reply.confirm is None
    # the last four turns: 8 minutes on still read shop #1, from 16 on none
    assert [t.incident_at for t in env.panel.turns.recent()] == [T0, None, None, None]
    await nothing_changed(env)


@pytest.mark.parametrize(
    ("text", "read", "offered"),
    [
        ("hm, not sure", {"kind": "unclear"}, False),
        ("restart it", {"kind": "refusal", "refusal": "change"}, False),
        ("don't restart", {"kind": "action", "action": ASKED["instruct"]}, False),
        ("tell shop #1 not to restart", {"kind": "action", "action": ASKED["instruct"]}, True),
        ("tell shop #12 to wait", {"kind": "action", "action": ASKED["instruct"]}, False),
    ],
)
async def test_a_follow_up_reads_the_incident_but_only_a_reply_or_its_name_reaches_the_plan(
    env, text, read, offered
):
    question = reading(kind="question", answer="shop is down; its plan waits for your yes.")
    await say(env, "what about shop?", question, incident_id=env.incident.id)
    env.clock.now = T0 + timedelta(minutes=5)

    reply = await say(env, text, reading(**read))

    assert '"name":"shop #1"' in incident_read(env, 1)  # read with the incident's facts
    assert (reply.confirm is not None) == offered
    if offered:
        assert reply.confirm is not None and reply.confirm.yes_label == "Send"
    await nothing_changed(env)


# --- the talk log (#411) ---


async def test_a_pressed_offer_lands_in_the_log_with_what_the_panel_showed_and_the_press(env):
    reply = await say(env, "leave shop alone", reading(kind="action", action=ASKED["snooze"]))
    assert reply.confirm is not None

    done = await env.panel.press(reply.confirm.id, True, env.actions)
    await env.panel.press(reply.confirm.id, True, env.actions)  # a second press ran nothing

    [entry] = env.talk.log.exchanges()
    assert (entry.surface, entry.text, entry.kind) == ("panel", "leave shop alone", "action")
    assert entry.offer is not None and entry.offer.target_id == SHOP
    assert (entry.answer, entry.links) == (reply.answer, reply.links)
    assert entry.press is not None
    assert (entry.press.yes, entry.press.line, entry.press.at) == (True, done.line, T0)


async def test_a_yes_to_a_plan_is_logged_as_the_line_the_panel_showed_instead(env):
    action = {"kind": "show_plan", "incident": "shop #1"}
    reply = await say(env, "yes do it", reading(kind="action", action=action))

    [entry] = env.talk.log.exchanges()
    assert entry.offer is not None and entry.offer.kind == "show_plan"
    on_page = prompts.load("talk/plan_on_page", incident="shop #1").strip()
    assert entry.answer == reply.answer == on_page
    assert entry.press is None and reply.confirm is None


async def test_a_declined_offer_lands_in_the_log_as_no(env):
    reply = await say(env, "leave shop alone", reading(kind="action", action=ASKED["leave_alone"]))
    assert reply.confirm is not None

    await env.panel.press(reply.confirm.id, False, env.actions)

    [entry] = env.talk.log.exchanges()
    assert entry.press is not None
    assert (entry.press.yes, entry.press.line) == (False, said("answer_confirm_no"))


async def test_a_second_press_while_the_first_runs_never_takes_its_place_in_the_log(env):
    reply = await say(env, "leave shop alone", reading(kind="action", action=ASKED["leave_alone"]))
    assert reply.confirm is not None
    run, release = env.actions.run, asyncio.Event()

    async def slow_run(offer):
        await release.wait()
        return await run(offer)

    env.actions.run = slow_run
    first = asyncio.create_task(env.panel.press(reply.confirm.id, True, env.actions))
    await asyncio.sleep(0)
    second = await env.panel.press(reply.confirm.id, True, env.actions)
    release.set()
    done, _ = await asyncio.gather(first, asyncio.sleep(0))

    assert second.line == said("answer_confirm_used")
    assert len(await env.quiet.active()) == 1  # the first press ran it
    [entry] = env.talk.log.exchanges()
    assert entry.press is not None
    assert (entry.press.yes, entry.press.line) == (True, done.line)
    assert done.line != said("answer_confirm_used")


async def test_an_expired_press_is_not_logged_as_the_offers_press(env):
    reply = await say(env, "leave shop alone", reading(kind="action", action=ASKED["leave_alone"]))
    assert reply.confirm is not None
    env.clock.now = T0 + CONFIRM_TTL

    await env.panel.press(reply.confirm.id, True, env.actions)

    assert env.talk.log.exchanges()[0].press is None


@pytest.mark.parametrize(
    ("replies", "kind", "line"),
    [
        ([reading(kind="refusal", refusal="change", answer="Sure!")], "refusal", "refused_change"),
        ([reading(kind="unclear")], "unclear", "unclear"),
        ([], "failed", "failed"),  # the scripted model has no answer: the call fails
    ],
    ids=["refused", "unclear", "failed"],
)
async def test_a_refused_an_unclear_and_a_failed_reading_land_in_the_log(env, replies, kind, line):
    reply = await say(env, "rm -rf shop, now", *replies)

    [entry] = env.talk.log.exchanges()
    assert (entry.surface, entry.text, entry.kind) == ("panel", "rm -rf shop, now", kind)
    assert entry.answer == reply.answer == prompts.load(f"talk/{line}").strip()
    assert entry.links == reply.links  # what the panel showed
    assert (entry.offer, entry.press, reply.confirm) == (None, None, None)
    if kind == "failed":
        assert entry.reading is None and entry.error is not None
    else:
        assert entry.reading is not None and entry.reading.kind == kind
        assert entry.error is None


async def test_a_press_whose_run_fails_is_logged_as_failed_by_the_errors_type(env):
    reply = await say(env, "leave shop alone", reading(kind="action", action=ASKED["leave_alone"]))
    assert reply.confirm is not None

    async def broken(offer):
        raise RuntimeError("database is locked at /data/bleep.db")

    env.actions.run = broken
    with pytest.raises(RuntimeError):
        await env.panel.press(reply.confirm.id, True, env.actions)

    [entry] = env.talk.log.exchanges()
    assert entry.press is not None
    failed = prompts.load("talk/log_press_failed", error="RuntimeError").strip()
    assert entry.press.line == failed and "locked" not in entry.press.line


def in_german(env) -> None:
    env.talk.language = env.actions.language = lambda: "de"


async def test_in_german_the_panel_offers_with_german_words_and_buttons(env):
    in_german(env)
    reply = await say(env, "lass shop in ruhe", reading(kind="action", action=ASKED["snooze"]))

    assert reply.answer == "🤫 shop bis 14:00 UTC stummschalten?"
    assert reply.confirm is not None
    assert (reply.confirm.yes_label, reply.confirm.no_label) == ("Ja", "Nein")
    assert reply.links == [TalkLink(kind="service", id=SHOP, label="shop")]  # names stay names

    done = await env.panel.press(reply.confirm.id, True, env.actions)
    again = await env.panel.press(reply.confirm.id, True, env.actions)
    assert done.line == "Stumm bis 14:00 UTC. Bleep lässt es bis dahin in Ruhe."
    assert again.line == "Schon beantwortet; das hat nichts mehr geändert."


async def test_in_german_a_refused_change_and_a_plan_on_its_page_are_german(env):
    in_german(env)
    refused = await say(env, "rm -rf /", reading(kind="refusal", refusal="change"))
    assert refused.answer.startswith("Aus dem Chat kann ich keine Befehle ausführen.")

    action = {"kind": "show_plan", "incident": "shop #1"}
    plan = await say(env, "zeig den plan", reading(kind="action", action=action))
    assert plan.answer.startswith("🙋 Der Plan von shop #1 wartet auf dein Ja.")
    assert plan.confirm is None


async def test_in_german_the_talk_log_export_stays_english(env):
    in_german(env)
    reply = await say(env, "lass shop in ruhe", reading(kind="action", action=ASKED["leave_alone"]))
    assert reply.confirm is not None
    await env.panel.press(reply.confirm.id, True, env.actions)

    text = transcript(env.talk.log.exchanges(), T0)
    assert "# Chat with Bleep" in text and "Pressed Yes" in text
