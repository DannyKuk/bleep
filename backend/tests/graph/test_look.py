"""A look on request in the graph's prompts (#410): a run the person's look alone opened tells
Investigate, Diagnose and Report that nothing had been seen failing; any other run's prompts stay
as they are."""

from test_diagnose import reply
from test_diagnose import setup as diagnose_setup
from test_investigate import DONE
from test_investigate import setup as investigate_setup
from test_report import NO_ACTION, written
from test_report import setup as report_setup

from bleep import prompts
from bleep.graph.look import bare_look, look_rule
from bleep.graph.nodes import diagnose, investigate, report
from bleep.schemas.incidents import LOOK_REQUESTED, Signal, Subject
from graph_env import T0

SUBJECT = Subject(kind="service", host_id="a", service_id="a/compose:shop")
LOOK = Signal(subject=SUBJECT, kind=LOOK_REQUESTED, at=T0, detail={"via": "dashboard"})
DIED = Signal(subject=SUBJECT, kind="container_died", at=T0, container="shop-web-1")


def test_only_a_run_the_look_alone_opened_is_a_bare_look():
    assert bare_look([LOOK])
    assert not bare_look([LOOK, DIED])  # a failure attached to the look: something broke
    assert not bare_look([DIED])
    assert not bare_look([])


def test_the_rule_is_the_nodes_prompt_file_for_a_bare_look_and_empty_otherwise():
    rule = look_rule("diagnose", [LOOK])
    assert rule == "\n\n" + prompts.load("graph/look_diagnose").strip()
    assert look_rule("diagnose", [LOOK, DIED]) == ""


def test_a_run_that_is_no_look_gets_the_prompt_files_as_they_were():
    text = prompts.file_text("graph/report")
    filled = {name: f"<{name}>" for name in prompts.placeholders(text)} | {"look": ""}
    assert prompts.load("graph/report", **filled) == text.replace("{look}", "").format(**filled)


def look_signals(state: dict) -> dict:
    subject = state["subject"]
    return state | {"signals": [LOOK.model_copy(update={"subject": subject})]}


async def test_investigate_is_told_a_look_names_where_it_was_asked_not_what_failed(tmp_path):
    env, state = await investigate_setup(tmp_path, [DONE])
    await investigate.make(env.deps)(look_signals(state))
    task = str(env.models.model.seen[0][1].content)
    assert "asked you to look at this service now" in task
    assert "not a part of the service" in task


async def test_investigate_of_a_failure_is_not_told_about_looks(tmp_path):
    env, state = await investigate_setup(tmp_path, [DONE])
    await investigate.make(env.deps)(state)
    assert "look_requested" not in str(env.models.model.seen[0][1].content)


async def test_diagnose_of_a_look_may_say_nothing_is_wrong(tmp_path):
    env, state = await diagnose_setup(tmp_path, [reply(0.9)])
    await diagnose.make(env.deps)(look_signals(state))
    sent = "\n".join(str(m.content) for m in env.models.model.seen[0])
    assert "says that nothing is wrong" in sent and "how sure you are that it is healthy" in sent


async def test_diagnose_of_a_failure_is_not_told_about_looks(tmp_path):
    env, state = await diagnose_setup(tmp_path, [reply(0.9)])
    await diagnose.make(env.deps)(state)
    assert "look_requested" not in "\n".join(str(m.content) for m in env.models.model.seen[0])


async def test_the_report_of_a_look_says_a_healthy_service_was_no_issue(tmp_path):
    env, state, _ = await report_setup(tmp_path, written())
    await report.make(env.deps)(look_signals(state) | {"proposal": NO_ACTION})
    sent = "\n".join(m.text for m in env.models.model.seen[0])
    assert "nothing had been seen failing" in sent and "Do not call it an issue" in sent


async def test_the_report_of_a_failure_is_not_told_about_looks(tmp_path):
    env, state, _ = await report_setup(tmp_path, written())
    await report.make(env.deps)(state | {"proposal": NO_ACTION})
    assert "nothing had been seen failing" not in "\n".join(
        m.text for m in env.models.model.seen[0]
    )
