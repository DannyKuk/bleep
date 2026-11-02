"""Structured output that survives models with weak or no tool support."""

import json
import re
from typing import Any

import openai
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel, ValidationError

from bleep import prompts

FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
RAW_LIMIT = 500


class StructuredOutputFailed(Exception):
    def __init__(self, raw: str) -> None:
        super().__init__(f"model did not return valid structured output: {raw[:120]!r}")
        self.raw = raw


def _first_json_object(text: str) -> Any:
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            return decoder.raw_decode(text, start)[0]
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
    raise ValueError("no JSON object found in the reply")


def parse_reply[T: BaseModel](schema: type[T], text: str) -> T:
    """Pull JSON from a fenced block or the bare reply and validate it."""
    for block in [*FENCE.findall(text), text]:
        try:
            data = _first_json_object(block)
        except ValueError:
            continue
        return schema.model_validate(data)
    raise ValueError("no JSON object found in the reply")


async def _via_tools[T: BaseModel](
    model: BaseChatModel, schema: type[T], messages: list[BaseMessage]
) -> T | None:
    try:
        result = await model.with_structured_output(schema).ainvoke(messages)
    except NotImplementedError, ValueError, openai.BadRequestError:
        return None  # ValueError covers parse and validation errors
    if isinstance(result, schema):
        return result
    if isinstance(result, dict):
        try:
            return schema.model_validate(result)
        except ValidationError:
            return None
    return None


async def invoke_structured[T: BaseModel](
    model: BaseChatModel, schema: type[T], messages: list[BaseMessage], retries: int = 1
) -> T:
    if (result := await _via_tools(model, schema, messages)) is not None:
        return result
    ask = prompts.load("llm/json_fallback", schema=json.dumps(schema.model_json_schema()))
    conversation = [*messages, HumanMessage(ask)]
    raw = ""
    for _ in range(retries + 1):
        reply = await model.ainvoke(conversation)
        raw = reply.text
        try:
            return parse_reply(schema, raw)
        except ValueError as error:  # includes pydantic ValidationError
            conversation = [
                *conversation,
                AIMessage(raw),
                HumanMessage(prompts.load("llm/json_retry", error=str(error))),
            ]
    raise StructuredOutputFailed(raw[:RAW_LIMIT])
