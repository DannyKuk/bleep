"""Fakes for engines, shells and chat models land here, so no test touches a real host or model."""

from collections.abc import Sequence
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


class FakeChatModel(BaseChatModel):
    """Replays a scripted list of `AIMessage`s (tool calls and usage metadata included).

    A real `BaseChatModel`, so LangGraph tool loops and `with_structured_output` work on it.
    Running past the end of the script raises, so a test never silently loops.
    """

    script: list[AIMessage]
    structured_output_unsupported: bool = False
    cursor: int = 0
    seen: list[list[BaseMessage]] = Field(default_factory=list)
    bound_tools: list[Any] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "fake-chat-model"

    def _next(self, messages: list[BaseMessage]) -> ChatResult:
        self.seen.append(list(messages))
        if self.cursor >= len(self.script):
            raise AssertionError(f"FakeChatModel script exhausted after {len(self.script)} replies")
        reply = self.script[self.cursor]
        self.cursor += 1
        return ChatResult(generations=[ChatGeneration(message=reply)])

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._next(messages)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._next(messages)

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> FakeChatModel:
        self.bound_tools = list(tools)
        return self

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        if self.structured_output_unsupported:
            raise NotImplementedError("this model has no tool support")
        return super().with_structured_output(schema, **kwargs)
