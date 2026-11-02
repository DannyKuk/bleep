import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from bleep.llm.structured import StructuredOutputFailed, invoke_structured
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
