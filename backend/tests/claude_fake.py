"""A real `ChatAnthropic` over a mock transport, for tests of what a call puts in its request.
Nothing touches the network."""

import json
from typing import Any

import anthropic
import httpx2
from pydantic import SecretStr

from bleep.llm.factory import chat_model
from bleep.schemas.settings import ModelSettings

KEY = "sk-ant-api03-test-key-0123456789"


def claude_answering(text: str) -> tuple[Any, list[dict]]:
    """A `ChatAnthropic` whose every reply is `text`, and the request bodies it is sent."""
    settings = ModelSettings(
        provider="anthropic",
        model="claude-sonnet-5-5",
        cheap_model="claude-sonnet-5-5",
        anthropic_api_key=SecretStr(KEY),
    )
    model = chat_model(settings, "main", timeout_s=60)
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

    model.__dict__["_async_client"] = anthropic.AsyncAnthropic(
        api_key=KEY,
        timeout=model.default_request_timeout,
        max_retries=model.max_retries,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    return model, bodies


def sent_no_cache_control(bodies: list[dict]) -> bool:
    """At least one request went out, and none asked for caching."""
    return bool(bodies) and all("cache_control" not in body for body in bodies)
