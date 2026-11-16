"""Helpers every read tool shares: its texts, argument errors, and guarding what it returns."""

import asyncio
from collections.abc import Callable
from copy import copy
from dataclasses import dataclass
from functools import cache
from typing import Any

from pydantic import BaseModel, ValidationError, create_model
from pydantic.fields import FieldInfo

from bleep.guard.pipeline import guard_text
from bleep.tools.registry import Texts, ToolContext


def said(texts: Texts, name: str, /, **values: str) -> str:
    """The prompt file `name` filled with `values`, without its closing line break: a tool's
    description, or a sentence it answers with instead of data."""
    return texts(name, **values).strip()


def say(ctx: ToolContext, name: str, /, **values: str) -> str:
    """`said` with the texts of the tool's context."""
    return said(ctx.texts, name, **values)


@dataclass(frozen=True)
class ArgText:
    """Names the prompt file that describes a tool argument to the model, in the argument's
    annotation: `container: Annotated[str, ArgText("tools/args/container")]`."""

    name: str


def described(ctx: ToolContext, schema: type[BaseModel]) -> type[BaseModel]:
    """`schema` with each argument's description loaded from its `ArgText` file, for a tool's
    `args_schema`: the model reads the descriptions in the tool's JSON schema."""
    texts = tuple(
        (field, say(ctx, _arg_text(schema, field, info).name))
        for field, info in schema.model_fields.items()
    )
    return _with_descriptions(schema, texts)


def _arg_text(schema: type[BaseModel], field: str, info: FieldInfo) -> ArgText:
    found = [m for m in info.metadata if isinstance(m, ArgText)]
    if len(found) != 1:
        raise TypeError(f"{schema.__name__}.{field} needs one ArgText naming its description")
    return found[0]


@cache
def _with_descriptions(
    schema: type[BaseModel], texts: tuple[tuple[str, str], ...]
) -> type[BaseModel]:
    """One class per schema and texts, so a tool built every Investigate round reuses it."""
    fields: dict[str, Any] = {}
    for field, text in texts:
        info = copy(schema.model_fields[field])
        info.description = text
        fields[field] = (info.annotation, info)
    return create_model(schema.__name__, __base__=schema, __module__=schema.__module__, **fields)


def untrusted_rule(ctx: ToolContext) -> str:
    """The one rule on untrusted output, for the `{untrusted_rule}` of every tool description
    whose tool returns text from outside Bleep."""
    return say(ctx, "untrusted/tool_output")


def invalid_arguments(ctx: ToolContext) -> Callable[[ValidationError], str]:
    """What a tool answers a call whose arguments do not validate (`handle_validation_error`)."""

    def answer(error: ValidationError) -> str:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}"
            for e in error.errors()
        )
        return say(ctx, "tools/reply/invalid_arguments", problems=problems)

    return answer


async def guard_result(ctx: ToolContext, source: str, text: str) -> str:
    """Redact, clean and wrap `text` off the event loop, so a hostile input cannot stall it."""
    wrapped, redactions = await asyncio.to_thread(guard_text, source, text)
    if redactions and ctx.guard_log is not None:
        ctx.guard_log.record(
            ctx.incident_id, "redacted", f"{redactions} secret(s) redacted from {source} output"
        )
    return wrapped
