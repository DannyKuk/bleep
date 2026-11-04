import asyncio

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from openrouter import errors as openrouter_errors
from pydantic import BaseModel

from bleep.llm.guard import ModelGuard, ModelUnavailable
from bleep.llm.structured import invoke_structured
from fakes import FakeChatModel


class Recorder:
    """A `sleep` that only records the delays asked for."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def guard(sleep: Recorder, timeout_s: float = 0.01) -> ModelGuard:
    return ModelGuard(timeout_s, retries=2, backoff_s=2.0, sleep=sleep)


def status_error(cls, status):
    request = httpx.Request("POST", "http://local/v1/chat/completions")
    return cls("nope", response=httpx.Response(status, request=request), body=None)


async def hang() -> None:
    await asyncio.Event().wait()


async def test_a_call_that_hangs_is_cut_off_and_retried_with_growing_waits_then_unavailable():
    sleep, attempts = Recorder(), []

    async def attempt():
        attempts.append(1)
        await hang()

    with pytest.raises(ModelUnavailable) as caught:
        await guard(sleep).call(attempt)

    assert len(attempts) == 3  # the call and two retries
    assert sleep.delays == [2.0, 4.0]
    assert caught.value.reason == "TimeoutError"


@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadTimeout("slow"),
        httpx.ConnectError("refused"),
        openai.APIConnectionError(request=httpx.Request("POST", "http://local/v1")),
        status_error(openai.RateLimitError, 429),
        status_error(openai.InternalServerError, 503),
    ],
)
async def test_a_transient_error_is_retried_and_the_next_answer_is_used(error):
    sleep, errors = Recorder(), [error]

    async def attempt():
        if errors:
            raise errors.pop()
        return "answer"

    assert await guard(sleep).call(attempt) == "answer"
    assert sleep.delays == [2.0]


@pytest.mark.parametrize(
    "error",
    [
        status_error(openai.BadRequestError, 400),
        status_error(openai.AuthenticationError, 401),
        ValueError("does not validate"),
    ],
)
async def test_an_error_retrying_cannot_fix_passes_through_at_once(error):
    sleep, attempts = Recorder(), []

    async def attempt():
        attempts.append(1)
        raise error

    with pytest.raises(type(error)):
        await guard(sleep).call(attempt)
    assert len(attempts) == 1 and sleep.delays == []


class Verdict(BaseModel):
    cause: str


class HangingModel(FakeChatModel):
    hangs: int = 0

    async def _agenerate(self, *args, **kwargs):
        if self.hangs > 0:
            self.hangs -= 1
            await hang()
        return await super()._agenerate(*args, **kwargs)


async def test_a_hanging_model_is_cut_off_by_invoke_structured_and_a_later_call_works():
    reply = AIMessage('{"cause": "oom"}')
    model = HangingModel(script=[reply], structured_output_unsupported=True, hangs=3)
    sleep = Recorder()
    ask = [HumanMessage("why?")]

    with pytest.raises(ModelUnavailable):
        await invoke_structured(model, Verdict, ask, guard=guard(sleep))
    assert model.hangs == 0 and sleep.delays == [2.0, 4.0]

    assert await invoke_structured(model, Verdict, ask, guard=guard(sleep)) == Verdict(cause="oom")


def openrouter_error(cls, status: int, body: str = "", content_type: str = "text/html"):
    response = httpx.Response(
        status,
        text=body,
        headers={"content-type": content_type},
        request=httpx.Request("POST", "http://x"),
    )
    return cls("bad gateway", response, body)


@pytest.mark.parametrize("status", [502, 504, 408, 429])
async def test_an_html_error_page_from_a_proxy_is_retried(status):
    sleep, errors = (
        Recorder(),
        [
            openrouter_error(
                openrouter_errors.OpenRouterDefaultError, status, "<html>bad gateway</html>"
            )
        ],
    )

    async def attempt():
        if errors:
            raise errors.pop()
        return "answer"

    assert await guard(sleep).call(attempt) == "answer"


@pytest.mark.parametrize("status", [400, 401, 404])
async def test_an_openrouter_client_error_is_not_retried(status):
    error = openrouter_error(openrouter_errors.OpenRouterDefaultError, status, "<html>no</html>")
    with pytest.raises(type(error)):
        await guard(Recorder()).call(lambda: _raise(error))


@pytest.mark.parametrize(
    "error", [httpx.UnsupportedProtocol("no scheme"), httpx.LocalProtocolError("bad")]
)
async def test_a_config_or_protocol_error_is_not_retried(error):
    sleep = Recorder()
    with pytest.raises(type(error)):
        await guard(sleep).call(lambda: _raise(error))
    assert sleep.delays == []


async def _raise(error: Exception):
    raise error
