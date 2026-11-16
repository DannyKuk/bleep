"""The read-only host shell tool for the Investigate step.

The command allowlist lives in `hosts.shell` and is enforced there; this tool only routes. Output
(journalctl above all) is untrusted, so it is redacted, cleaned and wrapped like the Docker tools'.
"""

import logging
from typing import Annotated

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from bleep.hosts.shell import COMMAND_TIMEOUT_S, CommandNotAllowed, allowed_text, run_allowed
from bleep.hosts.ssh import SshUnavailable
from bleep.tools.common import (
    ArgText,
    described,
    guard_result,
    invalid_arguments,
    say,
    untrusted_rule,
)
from bleep.tools.registry import ToolContext, ToolSpec

log = logging.getLogger(__name__)


class HostCommandArgs(BaseModel):
    command: Annotated[str, ArgText("tools/args/command")]


async def _host_command(ctx: ToolContext, command: str) -> str:
    shell = ctx.registry.shell(ctx.host_id)
    if shell is None:
        return say(ctx, "tools/reply/needs_ssh")
    try:
        uname = await shell.run(["uname", "-s"], COMMAND_TIMEOUT_S)
        if uname.exit_code != 0:
            failed = say(
                ctx,
                "tools/reply/uname_failed",
                code=str(uname.exit_code),
                stderr=uname.stderr.strip(),
            )
            error = await guard_result(ctx, "error", failed)
            return say(ctx, "tools/reply/host_unreachable", error=error)
        name = uname.stdout.strip()
        if name != "Linux" and name != "Darwin":
            system = await guard_result(ctx, "error", name)
            return say(ctx, "tools/reply/not_supported", system=system)
        text = await run_allowed(shell, command, name)
    except CommandNotAllowed as refusal:
        if ctx.guard_log is not None:
            ctx.guard_log.record(ctx.incident_id, "refused_command", command)
        return str(refusal)
    except SshUnavailable as error:
        reason = await guard_result(ctx, "error", str(error))
        return say(ctx, "tools/reply/host_unreachable", error=reason)
    except TimeoutError:
        return say(ctx, "tools/reply/timed_out")
    return await guard_result(ctx, "host_command", text or say(ctx, "tools/reply/no_output"))


def _build(ctx: ToolContext) -> StructuredTool:
    async def call(command: str) -> str:
        return await _host_command(ctx, command)

    return StructuredTool(
        name="host_command",
        description=say(
            ctx, "tools/host_command", commands=allowed_text(), untrusted_rule=untrusted_rule(ctx)
        ),
        args_schema=described(ctx, HostCommandArgs),
        coroutine=call,
        handle_validation_error=invalid_arguments(ctx),
    )


def _offered(ctx: ToolContext) -> bool:
    """A socket host has no shell, so the tool could only fail there."""
    return ctx.registry.shell(ctx.host_id) is not None


SPECS = [ToolSpec("host_command", "read", "host", _build, _offered)]
