"""The Anthropic API as a model provider (#504): the factory, the errors that pause an incident, and
the cost Claude calls record. The real `ChatAnthropic` runs against a mock transport; nothing
touches the network."""

import json
from datetime import datetime

import anthropic
import httpx2
import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, SecretStr

from bleep.llm.factory import chat_model
from bleep.llm.guard import ModelGuard, ModelUnavailable, is_transient
from bleep.llm.structured import invoke_structured
from bleep.llm.usage import CostLedger, UsageTracker
from bleep.schemas.settings import ModelSettings
from bleep.store.db import Database
from fakes import FakeChatModel

KEY = "sk-ant-api03-test-key-0123456789"


def claude(**choice) -> ModelSettings:
    return ModelSettings(
        provider="anthropic",
        model="claude-sonnet-5-5",
        cheap_model="claude-haiku-5-5",
        anthropic_api_key=SecretStr(KEY),
        **choice,
    )


def message_body(text="ok", model="claude-sonnet-5-5", tin=100, tout=20) -> dict:
    return {
        "id": "msg_01",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": tin, "output_tokens": tout},
    }


def over_transport(model: ChatAnthropic, handler) -> list[httpx2.Request]:
    """Point the model's client at `handler`, like an httpx.MockTransport; returns the requests."""
    seen: list[httpx2.Request] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    client = anthropic.AsyncAnthropic(
        api_key=KEY,
        timeout=model.default_request_timeout,
        max_retries=model.max_retries,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(record)),
    )
    model.__dict__["_async_client"] = client
    return seen


def status_error(cls, status):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("nope", response=httpx2.Response(status, request=request), body=None)


def test_the_anthropic_provider_builds_chat_anthropic_and_the_roles_pick_models():
    main, cheap = chat_model(claude(), "main"), chat_model(claude(), "cheap")
    assert isinstance(main, ChatAnthropic) and isinstance(cheap, ChatAnthropic)
    assert (main.model, cheap.model) == ("claude-sonnet-5-5", "claude-haiku-5-5")
    assert main.anthropic_api_key.get_secret_value() == KEY


def test_the_claude_ids_are_the_defaults_of_the_anthropic_provider():
    settings = ModelSettings(provider="anthropic", anthropic_api_key=SecretStr(KEY))
    main, cheap = chat_model(settings, "main"), chat_model(settings, "cheap")
    assert (main.model, cheap.model) == ("claude-haiku-5-5", "claude-haiku-5-5")


def test_an_id_the_person_chose_stays_with_the_anthropic_provider():
    settings = ModelSettings(provider="anthropic", model="claude-opus-5-5")
    assert chat_model(settings, "main").model == "claude-opus-5-5"
    assert chat_model(settings, "cheap").model == "claude-haiku-5-5"


def test_the_other_providers_keep_their_defaults():
    assert ModelSettings().resolved().model == "openai/gpt-6-luna"
    local = ModelSettings(provider="local").resolved()
    assert (local.model, local.cheap_model, local.local_model) == (
        "openai/gpt-6-luna",
        "openai/gpt-6-luna",
        "openai/gpt-oss-20b",
    )


def test_the_provider_alone_from_the_env_gives_the_claude_ids(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_CHEAP_MODEL", raising=False)
    models = ModelSettings().resolved()
    assert (models.model, models.cheap_model) == ("claude-haiku-5-5", "claude-haiku-5-5")


def test_the_model_has_no_temperature_because_claude_sonnet_5_5_rejects_one():
    model = chat_model(claude(temperature=0), "main")
    assert model.temperature is None
    model._assert_valid_model_configuration({})


def test_the_client_timeout_is_in_seconds_a_little_over_the_guards_and_without_retries():
    model = chat_model(claude(), "main", timeout_s=90)
    assert model.default_request_timeout == 95 and model.max_retries == 0


def test_the_key_is_read_from_the_env_by_the_model_settings(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    settings = ModelSettings()
    assert settings.provider == "anthropic"
    assert settings.anthropic_api_key.get_secret_value() == KEY


async def test_the_model_answers_through_the_messages_api_with_the_key_and_no_sampling():
    model = chat_model(claude(), "main", timeout_s=60)
    seen = over_transport(model, lambda request: httpx2.Response(200, json=message_body("hi")))

    reply = await model.ainvoke([HumanMessage("hello")])

    assert reply.text == "hi"
    body = json.loads(seen[0].content)
    assert body["model"] == "claude-sonnet-5-5" and "temperature" not in body


@pytest.mark.parametrize(
    "error",
    [
        anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x/v1/messages")),
        anthropic.APITimeoutError(request=httpx2.Request("POST", "https://x/v1/messages")),
        status_error(anthropic.RateLimitError, 429),
        status_error(anthropic.InternalServerError, 500),
        status_error(anthropic.ServiceUnavailableError, 503),
        status_error(anthropic.OverloadedError, 529),
        status_error(anthropic.APIStatusError, 502),
        status_error(anthropic.RequestTimeoutError, 408),
    ],
)
def test_the_anthropic_errors_of_an_outage_are_transient(error):
    assert is_transient(error)


@pytest.mark.parametrize(
    "error",
    [
        status_error(anthropic.BadRequestError, 400),
        status_error(anthropic.AuthenticationError, 401),
        status_error(anthropic.PermissionDeniedError, 403),
        status_error(anthropic.NotFoundError, 404),
    ],
)
def test_the_anthropic_errors_retrying_cannot_fix_are_not(error):
    assert not is_transient(error)


class Recorder:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def guard(sleep: Recorder) -> ModelGuard:
    return ModelGuard(5, retries=2, backoff_s=2.0, sleep=sleep)


async def test_an_overloaded_claude_is_retried_and_the_next_answer_is_used():
    model = chat_model(claude(), "main", timeout_s=60)
    answers = [httpx2.Response(200, json=message_body("back")), httpx2.Response(529, json={})]
    seen = over_transport(model, lambda request: answers.pop())
    sleep = Recorder()

    reply = await guard(sleep).call(lambda: model.ainvoke([HumanMessage("a")]))

    assert reply.text == "back" and len(seen) == 2 and sleep.delays == [2.0]


@pytest.mark.parametrize("status", [429, 500, 529])
async def test_a_claude_that_stays_down_ends_in_model_unavailable(status):
    model = chat_model(claude(), "main", timeout_s=60)
    seen = over_transport(model, lambda request: httpx2.Response(status, json={}))
    sleep = Recorder()

    with pytest.raises(ModelUnavailable):
        await guard(sleep).call(lambda: model.ainvoke([HumanMessage("a")]))

    assert len(seen) == 3 and sleep.delays == [2.0, 4.0]  # the call and two retries


async def test_a_rejected_key_is_not_retried():
    model = chat_model(claude(), "main", timeout_s=60)
    seen = over_transport(model, lambda request: httpx2.Response(401, json={}))

    with pytest.raises(anthropic.AuthenticationError):
        await guard(Recorder()).call(lambda: model.ainvoke([HumanMessage("a")]))

    assert len(seen) == 1


class Verdict(BaseModel):
    cause: str


async def test_structured_output_asks_for_json_when_the_tool_call_fails_as_a_bad_request():
    model = chat_model(claude(), "main", timeout_s=60)

    def handler(request: httpx2.Request) -> httpx2.Response:
        if "tools" in json.loads(request.content):
            return httpx2.Response(400, json={"type": "error", "error": {"message": "no tools"}})
        return httpx2.Response(200, json=message_body('{"cause": "oom"}'))

    over_transport(model, handler)

    assert await invoke_structured(model, Verdict, [HumanMessage("why")]) == Verdict(cause="oom")


async def test_structured_output_on_sonnet_5_5_falls_back_to_json_without_forcing_the_tool():
    """Sonnet 5.5 refuses a forced tool choice; langchain-anthropic then leaves it out, and a
    reply without a tool call must end in the JSON fallback, not in an error."""
    model = chat_model(claude(), "main", timeout_s=60)
    bodies: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        return httpx2.Response(200, json=message_body('{"cause": "disk full"}'))

    over_transport(model, handler)

    result = await invoke_structured(model, Verdict, [HumanMessage("why")])

    assert result == Verdict(cause="disk full")
    assert all("tool_choice" not in body for body in bodies)


async def test_structured_output_reads_the_tool_call_claude_makes():
    model = chat_model(claude(), "main", timeout_s=60)
    body = message_body()
    body["content"] = [
        {"type": "tool_use", "id": "toolu_1", "name": "Verdict", "input": {"cause": "bad config"}}
    ]
    body["stop_reason"] = "tool_use"
    seen = over_transport(model, lambda request: httpx2.Response(200, json=body))

    assert await invoke_structured(model, Verdict, [HumanMessage("why")]) == Verdict(
        cause="bad config"
    )
    assert len(seen) == 1  # no JSON fallback


@pytest.fixture
async def ledger(tmp_path):
    db = Database(tmp_path / "bleep.db")
    await db.migrate()
    return CostLedger(db, clock=lambda: datetime(2026, 11, 12, 12, 0))


def anthropic_reply(model_name: str, tin: int, tout: int) -> AIMessage:
    """What ChatAnthropic returns: tokens in `usage_metadata`, the model id in `model_name`, and
    no cost."""
    return AIMessage(
        content="ok",
        usage_metadata={"input_tokens": tin, "output_tokens": tout, "total_tokens": tin + tout},
        response_metadata={"model_name": model_name, "stop_reason": "end_turn"},
    )


async def record(ledger, reply: AIMessage) -> None:
    model = FakeChatModel(script=[reply])
    await model.ainvoke([HumanMessage("a")], config={"callbacks": [UsageTracker("i", ledger)]})


async def test_a_claude_call_records_tokens_and_a_cost_from_the_price_table(ledger):
    await record(ledger, anthropic_reply("claude-sonnet-5-5", 1_000_000, 100_000))
    total = await ledger.incident_total("i")
    assert (total.tokens_in, total.tokens_out) == (1_000_000, 100_000)
    assert total.usd == pytest.approx(2.0 + 1.0)


async def test_claude_opus_5_5_is_priced_from_the_table(ledger):
    await record(ledger, anthropic_reply("claude-opus-5-5", 1_000_000, 100_000))
    assert (await ledger.incident_total("i")).usd == pytest.approx(4.0 + 2.0)


async def test_a_dated_id_of_the_model_is_priced_as_the_model(ledger):
    await record(ledger, anthropic_reply("claude-sonnet-5-5-20261001", 500_000, 0))
    assert (await ledger.incident_total("i")).usd == pytest.approx(1.0)


async def test_claude_haiku_5_5_is_cheap_up_to_a_prompt_of_100000_tokens(ledger):
    await record(ledger, anthropic_reply("claude-haiku-5-5", 100_000, 10_000))
    assert (await ledger.incident_total("i")).usd == pytest.approx(0.01 + 0.005)


async def test_claude_haiku_5_5_prices_the_whole_call_higher_for_a_prompt_over_100000(ledger):
    await record(ledger, anthropic_reply("claude-haiku-5-5", 100_001, 10_000))
    assert (await ledger.incident_total("i")).usd == pytest.approx(0.0500005 + 0.025)


async def test_a_call_through_the_real_client_records_its_usage(ledger):
    model = chat_model(claude(), "cheap", timeout_s=60)
    over_transport(
        model,
        lambda request: httpx2.Response(
            200, json=message_body(model="claude-haiku-5-5", tin=2_000_000, tout=1_000_000)
        ),
    )

    await model.ainvoke([HumanMessage("a")], config={"callbacks": [UsageTracker("i", ledger)]})

    total = await ledger.incident_total("i")
    assert (total.tokens_in, total.tokens_out) == (2_000_000, 1_000_000)
    assert total.usd == pytest.approx(1.0 + 2.5)  # at the long-prompt prices
