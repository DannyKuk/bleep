"""Which Anthropic calls ask for prompt caching (#546): only the investigate loop, the one call
site that sends the same prefix again. The real `ChatAnthropic` runs against a mock transport and
the requests are read; nothing touches the network."""

import json
import re
from pathlib import Path

import anthropic
import httpx2
import pytest
from pydantic import SecretStr
from test_diagnose import reply as diagnosis_reply
from test_diagnose import setup as diagnose_setup
from test_investigate import setup as investigate_setup

from bleep.graph.nodes import diagnose, investigate
from bleep.llm.factory import ANTHROPIC_CACHE, cached, chat_model
from bleep.schemas.settings import ModelSettings

KEY = "sk-ant-api03-test-key-0123456789"


def claude(model: str = "claude-sonnet-5-5") -> ModelSettings:
    return ModelSettings(
        provider="anthropic", model=model, cheap_model=model, anthropic_api_key=SecretStr(KEY)
    )


def answering(text: str) -> tuple[object, list[dict]]:
    """A `ChatAnthropic` whose every reply is `text`, and the request bodies it was sent."""
    model = chat_model(claude(), "main", timeout_s=60)
    bodies: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "msg_01",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-5-5",
                "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 100, "output_tokens": 20},
            },
        )

    client = anthropic.AsyncAnthropic(
        api_key=KEY,
        timeout=model.default_request_timeout,
        max_retries=model.max_retries,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    model.__dict__["_async_client"] = client
    return model, bodies


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
    env.models.model, bodies = answering("Nothing more to look at.")

    await investigate.make(env.deps)(state)

    assert bodies and all(body["cache_control"] == {"type": "ephemeral"} for body in bodies)
    assert bodies[0]["tools"]  # the tools it re-sends beside the breakpoint


async def test_diagnose_does_not_send_cache_control(tmp_path):
    env, state = await diagnose_setup(tmp_path, [])
    env.models.model, bodies = answering(diagnosis_reply(0.9).text)

    await diagnose.make(env.deps)(state)

    assert bodies and all("cache_control" not in body for body in bodies)


def test_only_the_investigate_node_asks_for_caching():
    """Propose, report, talk and the watcher's explain build their calls like diagnose does, so
    what keeps them uncached is that nothing but investigate turns it on."""
    source = Path(investigate.__file__).parents[2]  # bleep/
    callers = {
        path.relative_to(source).as_posix()
        for path in source.rglob("*.py")
        if re.search(r"(?<![\w.])cached\(", path.read_text()) and path.name != "factory.py"
    }
    assert callers == {"graph/nodes/investigate.py"}
