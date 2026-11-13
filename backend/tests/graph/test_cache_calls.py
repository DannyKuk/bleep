"""Which Anthropic calls ask for prompt caching (#546): only the investigate loop, the one call
site that sends the same prefix again. The real `ChatAnthropic` runs against a mock transport and
the requests are read; nothing touches the network. Talk and the watcher's explain are in
`tests/talk/test_talk_cache_call.py` and `tests/watcher/test_explain_cache_call.py`."""

import json
import re
from pathlib import Path

import pytest
from test_diagnose import reply as diagnosis_reply
from test_diagnose import setup as diagnose_setup
from test_investigate import setup as investigate_setup
from test_propose import restart
from test_propose import setup as propose_setup
from test_report import ACT, RESULT, decide, written
from test_report import setup as report_setup

from bleep.graph.nodes import diagnose, investigate, propose, report
from bleep.llm.factory import ANTHROPIC_CACHE, cached, chat_model
from bleep.schemas.settings import ModelSettings
from claude_fake import claude_answering, sent_no_cache_control

BLEEP = Path(investigate.__file__).parents[2]


def claude() -> ModelSettings:
    return ModelSettings(provider="anthropic", anthropic_api_key="sk-ant-api03-test-key-0123456789")


@pytest.mark.parametrize("role", ["main", "cheap"])
def test_a_model_from_the_factory_does_not_cache(role):
    assert "cache_control" not in (chat_model(claude(), role).model_kwargs or {})


def test_the_cached_model_is_a_copy_and_the_original_stays_uncached():
    plain = chat_model(claude(), "main")

    on = cached(plain)

    assert on.model_kwargs == ANTHROPIC_CACHE
    assert "cache_control" not in plain.model_kwargs


def test_a_model_of_another_provider_comes_back_as_it_is():
    local = chat_model(ModelSettings(provider="local"), "main")
    assert cached(local) is local


async def test_the_investigate_loop_sends_cache_control(tmp_path):
    env, state = await investigate_setup(tmp_path, [])
    env.models.model, bodies = claude_answering("Nothing more to look at.")

    await investigate.make(env.deps)(state)

    assert bodies and all(body["cache_control"] == {"type": "ephemeral"} for body in bodies)
    assert bodies[0]["tools"]  # the tools it re-sends beside the breakpoint


async def test_diagnose_does_not_send_cache_control(tmp_path):
    env, state = await diagnose_setup(tmp_path, [])
    env.models.model, bodies = claude_answering(diagnosis_reply(0.9).text)

    await diagnose.make(env.deps)(state)

    assert sent_no_cache_control(bodies)


async def test_propose_does_not_send_cache_control(tmp_path):
    env, state = await propose_setup(tmp_path)
    plan = {"kind": "act", "steps": [restart("shop-web-1")], "reason": "restart it"}
    env.models.model, bodies = claude_answering(json.dumps(plan | {"verification": {}}))

    update = await propose.make(env.deps)(state)

    assert update["proposal"].kind == "act"  # the model's reply was used
    assert sent_no_cache_control(bodies)


async def test_report_does_not_send_cache_control(tmp_path):
    env, state, _ = await report_setup(tmp_path)
    env.models.model, bodies = claude_answering(written().text)
    state |= {
        "proposal": ACT,
        "decision": decide("approve"),
        "step_results": [RESULT],
        "outcome": "resolved",
    }

    update = await report.make(env.deps)(state)

    assert update["report"].summary == "shop-web was down and Bleep started it again."
    assert sent_no_cache_control(bodies)


def test_only_the_investigate_node_asks_for_caching():
    """Talk, the watcher's explain and the rest build their calls through `chat_model()`, so what
    keeps them uncached is that nothing but investigate turns caching on. Read the sources: any
    call of `cached` (bare, qualified or aliased) or use of `ANTHROPIC_CACHE` or `cache_control`
    outside the factory is a way around it."""
    ways = re.compile(
        r"\bcached\(|import[^\n(]*\bcached\b|^\s+cached,?$|ANTHROPIC_CACHE|cache_control", re.M
    )
    users = {
        path.relative_to(BLEEP).as_posix()
        for path in BLEEP.rglob("*.py")
        if path.relative_to(BLEEP).as_posix() != "llm/factory.py" and ways.search(path.read_text())
    }
    assert users == {"graph/nodes/investigate.py"}
