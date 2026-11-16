"""The read-only host shell tool for the Investigate step.

The command allowlist lives in `hosts.shell` and is enforced there; this tool only routes. Output
(journalctl above all) is untrusted, so it is redacted, cleaned and wrapped like the Docker tools'.
"""

import logging

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from bleep.hosts.shell import CommandNotAllowed, allowed_text, run_allowed
from bleep.hosts.ssh import SshUnavailable
from bleep.tools.common import guard_result, invalid_arguments, say, untrusted_rule
from bleep.tools.registry import ToolContext, ToolSpec

log = logging.getLogger(__name__)

NEEDS_SSH = "host commands need an SSH host"


class HostCommandArgs(BaseModel):
    command: str = Field(description="One of the allowed commands, typed exactly as listed.")


async def _host_command(ctx: ToolContext, command: str) -> str:
    shell = ctx.registry.shell(ctx.host_id)
    if shell is None:
        return NEEDS_SSH
    try:
        name = (await shell.run(["uname", "-s"], 20.0)).stdout.strip()
        if name != "Linux" and name != "Darwin":
            return f"host commands are not supported on {await guard_result(ctx, 'error', name)}"
        text = await run_allowed(shell, command, name)
    except CommandNotAllowed as refusal:
        if ctx.guard_log is not None:
            ctx.guard_log.record(ctx.incident_id, "refused_command", command)
        return str(refusal)
    except SshUnavailable as error:
        return f"host unreachable: {await guard_result(ctx, 'error', str(error))}"
    except TimeoutError:
        return "the command timed out"
    return await guard_result(ctx, "host_command", text or "(no output)")


def _build(ctx: ToolContext) -> StructuredTool:
    async def call(command: str) -> str:
        return await _host_command(ctx, command)

    return StructuredTool(
        name="host_command",
        description=say(
            ctx, "tools/host_command", commands=allowed_text(), untrusted_rule=untrusted_rule(ctx)
        ),
        args_schema=HostCommandArgs,
        coroutine=call,
        handle_validation_error=invalid_arguments(ctx),
    )


def _offered(ctx: ToolContext) -> bool:
    """A socket host has no shell, so the tool could only fail there."""
    return ctx.registry.shell(ctx.host_id) is not None


SPECS = [ToolSpec("host_command", "read", "host", _build, _offered)]
