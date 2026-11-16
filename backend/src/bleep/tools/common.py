"""Helpers every read tool shares: its texts, argument errors, and guarding what it returns."""

import asyncio
from collections.abc import Callable

from pydantic import ValidationError

from bleep.guard.pipeline import guard_text
from bleep.tools.registry import ToolContext


def say(ctx: ToolContext, name: str, /, **values: str) -> str:
    """The prompt file `name` filled with `values`, without its closing line break: a tool's
    description, or a sentence it answers with instead of data."""
    return ctx.texts(name, **values).strip()


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
