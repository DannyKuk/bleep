"""A bare look at a service with no URL in Bleep's settings tells Investigate and Diagnose so in a
line of its own (#445), where a service with URLs gets its URL checks; any other run's prompts
stay byte for byte as they were."""

import re
from pathlib import Path

import pytest
from test_diagnose import reply
from test_investigate import DONE
from test_look import DIED, LOOK
from test_look_urls import gathered, on, url_env

from bleep.graph.nodes import diagnose, gather, investigate
from bleep.schemas.incidents import Signal, Subject
from fakes import FakeChatModel
from graph_env import graph_env

FIXTURES = Path(__file__).parent.parent / "fixtures" / "prompts"
NO_URL = "No URL is set for this service in Bleep, so there is no URL to check."


def stable(text: str) -> str:
    """The prompt with the random id of each untrusted wrapper replaced."""
    return re.sub(r'id="[0-9a-f]+"', 'id="X"', text)


async def rendered(env, subject: Subject, signals: list[Signal]) -> tuple[str, str]:
    """Investigate's task and Diagnose's messages for a run opened by `signals`."""
    env.models.model = FakeChatModel(script=[DONE, reply(0.9)], structured_output_unsupported=True)
    state: dict = {"incident_id": "inc-1", "subject": subject, "signals": signals}
    state |= await gather.make(env.deps)(state)
    await investigate.make(env.deps)(state)
    await diagnose.make(env.deps)(state | {"investigate_rounds": 1})
    task = str(env.models.model.seen[0][1].content)
    sent = "\n".join(str(m.content) for m in env.models.model.seen[1])
    return stable(task), stable(sent)


async def bare_shop(tmp_path):
    env = await graph_env(tmp_path)
    return env, Subject(kind="service", host_id="a", service_id=env.service_id("shop"))


def check_fixture(name: str, text: str) -> None:
    path = FIXTURES / name
    assert text.encode() == path.read_bytes()


async def test_a_look_at_a_service_with_urls_is_rendered_as_it_was(tmp_path):
    env, subject = await url_env(tmp_path, [])

    task, sent = await rendered(env, subject, [on(LOOK, subject)])

    check_fixture("graph-investigate-look-urls.txt", task)
    check_fixture("graph-diagnose-look-urls.txt", sent)
    assert NO_URL not in task + sent


async def test_a_failure_at_a_service_without_urls_is_rendered_as_it_was(tmp_path):
    env, subject = await bare_shop(tmp_path)

    task, sent = await rendered(env, subject, [on(DIED, subject)])

    check_fixture("graph-investigate-failure-no-urls.txt", task)
    check_fixture("graph-diagnose-failure-no-urls.txt", sent)
    assert NO_URL not in task + sent


async def test_gather_notes_a_look_at_a_service_without_urls_and_no_other_run(tmp_path):
    env, subject = await bare_shop(tmp_path)
    assert (await gathered(env, subject, [on(LOOK, subject)])).no_urls
    assert not (await gathered(env, subject, [on(DIED, subject)])).no_urls
    assert not (await gathered(env, subject, [on(LOOK, subject), on(DIED, subject)])).no_urls
    withs, with_subject = await url_env(tmp_path / "urls", [])
    assert not (await gathered(withs, with_subject, [on(LOOK, with_subject)])).no_urls


async def test_a_look_at_a_service_without_urls_says_so_in_investigate_and_diagnose(tmp_path):
    env, subject = await bare_shop(tmp_path)

    task, sent = await rendered(env, subject, [on(LOOK, subject)])

    section = task.split("## URL checks", 1)[1].split("## Host", 1)[0]
    assert NO_URL in section and "<untrusted" not in section
    assert "URL checks, which Bleep ran" not in sent
    assert f"URL checks: {NO_URL}" in sent


@pytest.mark.parametrize("signals", ["failure", "look and failure"])
async def test_a_run_a_failure_opened_is_not_told_about_urls(tmp_path, signals):
    env, subject = await bare_shop(tmp_path)
    opened = [on(DIED, subject)]
    if signals != "failure":
        opened = [on(LOOK, subject), *opened]

    task, sent = await rendered(env, subject, opened)

    assert "## URL checks" not in task and NO_URL not in task + sent
