import httpx
import openai
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel

from bleep.llm.structured import StructuredOutputFailed, invoke_structured
from bleep.llm.usage import CostLedger, UsageTracker
from bleep.store.db import Database
from fakes import FakeChatModel


class Verdict(BaseModel):
    cause: str
    confidence: float


ASK = [HumanMessage("why did it crash?")]
FENCED = 'Sure!\n```json\n{"cause": "oom", "confidence": 0.9}\n```'


async def test_uses_tool_call_when_the_model_supports_it():
    call = {"name": "Verdict", "args": {"cause": "oom", "confidence": 0.8}, "id": "1"}
    model = FakeChatModel(script=[AIMessage(content="", tool_calls=[call])])
    assert await invoke_structured(model, Verdict, ASK) == Verdict(cause="oom", confidence=0.8)


async def test_falls_back_to_fenced_json_when_model_has_no_tool_support():
    model = FakeChatModel(script=[AIMessage(FENCED)], structured_output_unsupported=True)
    assert await invoke_structured(model, Verdict, ASK) == Verdict(cause="oom", confidence=0.9)
    sent = model.seen[0]
    assert sent[: len(ASK)] == ASK
    assert "cause" in sent[-1].content  # the schema is in the instruction


async def test_falls_back_when_model_answers_text_instead_of_a_tool_call():
    model = FakeChatModel(script=[AIMessage(FENCED), AIMessage(FENCED)])
    assert (await invoke_structured(model, Verdict, ASK)).cause == "oom"


async def test_bare_json_reply_is_accepted():
    model = FakeChatModel(
        script=[AIMessage('{"cause": "disk", "confidence": 0.5} hope that helps')],
        structured_output_unsupported=True,
    )
    assert (await invoke_structured(model, Verdict, ASK)).cause == "disk"


async def test_retries_once_with_the_validation_error():
    model = FakeChatModel(
        script=[AIMessage('{"cause": "oom"}'), AIMessage(FENCED)],
        structured_output_unsupported=True,
    )
    assert (await invoke_structured(model, Verdict, ASK)).confidence == 0.9
    assert "confidence" in model.seen[1][-1].content


async def test_garbage_raises_after_the_retry_with_truncated_text():
    garbage = "no json here " * 200
    model = FakeChatModel(
        script=[AIMessage(garbage), AIMessage(garbage)], structured_output_unsupported=True
    )
    with pytest.raises(StructuredOutputFailed) as caught:
        await invoke_structured(model, Verdict, ASK)
    assert model.cursor == 2
    assert 0 < len(caught.value.raw) <= 500


class RejectingTools(FakeChatModel):
    error: Exception

    def with_structured_output(self, schema, **kwargs):
        def boom(_):
            raise self.error

        return RunnableLambda(boom)


def _status_error(cls, status):
    request = httpx.Request("POST", "http://local/v1/chat/completions")
    return cls("nope", response=httpx.Response(status, request=request), body=None)


@pytest.mark.parametrize(
    ("cls", "status"),
    [
        (openai.BadRequestError, 400),
        (openai.NotFoundError, 404),
        (openai.UnprocessableEntityError, 422),
    ],
)
async def test_provider_rejecting_tools_falls_back_to_json(cls, status):
    model = RejectingTools(script=[AIMessage(FENCED)], error=_status_error(cls, status))
    assert (await invoke_structured(model, Verdict, ASK)).cause == "oom"


@pytest.mark.parametrize(
    ("cls", "status"), [(openai.AuthenticationError, 401), (openai.RateLimitError, 429)]
)
async def test_auth_and_rate_limit_errors_propagate(cls, status):
    model = RejectingTools(script=[AIMessage(FENCED)], error=_status_error(cls, status))
    with pytest.raises(cls):
        await invoke_structured(model, Verdict, ASK)
    assert model.cursor == 0


async def test_connection_error_propagates():
    request = httpx.Request("POST", "http://local/v1")
    model = RejectingTools(script=[], error=openai.APIConnectionError(request=request))
    with pytest.raises(openai.APIConnectionError):
        await invoke_structured(model, Verdict, ASK)


async def test_all_three_calls_carry_the_tracker(tmp_path):
    db = Database(tmp_path / "b.db")
    await db.migrate()
    ledger = CostLedger(db)
    usage = {"input_tokens": 10, "output_tokens": 1, "total_tokens": 11}
    model = FakeChatModel(
        script=[
            AIMessage("no tool call", usage_metadata=usage),  # tool path: text, not a tool call
            AIMessage('{"cause": "oom"}', usage_metadata=usage),  # fallback: invalid
            AIMessage(FENCED, usage_metadata=usage),  # retry: valid
        ]
    )
    config = {"callbacks": [UsageTracker("inc", ledger)]}
    await invoke_structured(model, Verdict, ASK, config=config)
    assert model.cursor == 3
    assert (await ledger.incident_total("inc")).tokens == 33
