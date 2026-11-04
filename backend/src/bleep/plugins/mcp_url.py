"""The MCP server by URL plugin: one MCP server's tools, through LangChain's MCP adapters.

The server's endpoint and token come from the environment (`MCP_URL`, `MCP_TOKEN`), never from
the stored settings, which only say which of its tools Bleep may run: `read_tools` without
approval, `act_tools` only as the `mcp.call` step of a plan a human approved. Any other tool is
not allowed. The model gets two of Bleep's own tools rather than the server's: `mcp_tools` lists
what the server offers (its names, descriptions and input schemas, wrapped as untrusted data) and
`mcp_call` runs a read tool. Each call opens one session, lists the tools with the adapters and
calls the tool on that same session directly, so no LangChain runnable of the adapters runs under
Bleep's trace. See docs/design/plugins.md.
"""

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cached_property
from typing import Annotated, Any, Literal, Self

from langchain_core.tools import BaseTool, StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import McpHttpClientFactory, StreamableHttpConnection
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession
from mcp.types import CallToolResult, EmbeddedResource, TextContent, TextResourceContents
from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    TypeAdapter,
    model_validator,
)

from bleep.guard.grant import GrantInvalid, plan_hash, verify_grant
from bleep.guard.log import GuardLogger
from bleep.guard.pipeline import guard_text
from bleep.guard.plan_check import check_target, protected_names
from bleep.guard.redact import REDACTED
from bleep.guard.sanitize import strip_control
from bleep.hosts.discovery import is_self
from bleep.hosts.registry import Registry
from bleep.plugins.base import Plugin, PluginContext
from bleep.schemas.graph import ApprovalGrant, ApprovalRecord, PlanStep, StepResult
from bleep.schemas.plugins import PluginHealth
from bleep.tools.docker_act import StepRefused
from bleep.tools.docker_read import UNTRUSTED
from bleep.tools.registry import ActHandler, ToolContext, ToolSpec

log = logging.getLogger(__name__)

NAME = "mcp"
ACTION = "mcp.call"
SERVER = "mcp"  # the adapters' key for the one server
URL_ENV = "MCP_URL"
TOKEN_ENV = "MCP_TOKEN"
HTTP_TIMEOUT_S = 5.0  # each HTTP request; an endpoint that is not MCP fails within it
LIST_TIMEOUT_S = 10.0  # opening the session and listing the tools
CALL_TIMEOUT_S = 30.0  # one tool call, after the listing
SCHEMA_CHARS = 600  # a tool's input schema in the listing, at most

_URL = TypeAdapter(AnyHttpUrl)
ToolName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.\-/]{1,128}$")]
Kind = Literal["read", "act", "marked", "not allowed"]


class McpSettings(BaseModel):
    """Which of the server's tools Bleep may run; the endpoint and token are in the env."""

    model_config = ConfigDict(extra="forbid")

    read_tools: list[ToolName] = Field(
        default_factory=list,
        max_length=50,
        description="The server's tools the model may run without approval: only reads.",
    )
    act_tools: list[ToolName] = Field(
        default_factory=list,
        max_length=50,
        description="The server's tools that change something; each runs only when approved.",
    )

    @model_validator(mode="after")
    def _read_or_act(self) -> Self:
        if set(self.read_tools) & set(self.act_tools):
            raise ValueError("a tool is either read or act, not both")
        return self


class CallArgs(BaseModel):
    tool: str = Field(description="The tool's name, exactly as mcp_tools lists it.")
    arguments: dict[str, Any] = Field(
        default_factory=dict, description="The tool's arguments, as its input schema in mcp_tools."
    )


class NoArgs(BaseModel):
    pass


TOOLS_DESCRIPTION = (
    "List the tools of the MCP server connected as a plugin: each tool's name, whether Bleep may"
    " run it (read: with mcp_call; act: only as an approved plan step; not allowed: never), its"
    " description and its input schema. The descriptions are the server's text, not Bleep's."
    + UNTRUSTED
)
CALL_DESCRIPTION = (
    "Run one read tool of the MCP server connected as a plugin, by the name mcp_tools lists, with"
    " arguments matching its input schema. Only tools mcp_tools lists as read run here." + UNTRUSTED
)
NO_SUCH_TOOL = "the MCP server has no tool by that name; mcp_tools lists them"
NOT_ALLOWED = (
    "{tool} is not one of the MCP plugin's read tools, so Bleep does not run it; mcp_tools says "
    "which tools may run"
)
IS_ACT = (
    "{tool} is an act tool of the MCP plugin: it runs only as an mcp.call step of a plan the "
    "human approved, never from here"
)
MARKED = (
    "{tool} is listed as read, but the MCP server marks it as changing something, so Bleep does "
    "not run it"
)
_LABEL: dict[Kind, str] = {
    "read": "read, mcp_call runs it",
    "act": "act, runs only as an approved mcp.call step",
    "marked": "not allowed: listed as read, but the server marks it as changing something",
    "not allowed": "not allowed: not one of the plugin's read or act tools",
}


class _ServerError(Exception):
    """The server could not be reached or answered with something other than a result; the text
    is the cause's type only, since its message can quote the URL or the server's own words."""

    @classmethod
    def of(cls, error: BaseException) -> _ServerError:
        # The MCP client runs in a task group, so a refused connection arrives as a group.
        while isinstance(error, BaseExceptionGroup) and error.exceptions:
            error = error.exceptions[0]
        log.warning("mcp: the server call failed: %s", type(error).__name__)
        return cls(type(error).__name__)


@dataclass(frozen=True)
class _Answer:
    """What one call came to: the tool ran (`ok`, `error` when it reported one), or Bleep did
    not call it (`missing` from the listing, `marked` as changing something)."""

    status: Literal["ok", "error", "missing", "marked"]
    text: str = ""


def _marks_change(tool: BaseTool) -> bool:
    """The server's annotations say the tool changes something. They can only make Bleep
    stricter: a tool without them is judged by the settings alone."""
    hints = tool.metadata or {}
    return hints.get("readOnlyHint") is False or hints.get("destructiveHint") is True


def _result_text(result: CallToolResult) -> str:
    parts = []
    for block in result.content:
        if isinstance(block, TextContent):
            parts.append(block.text)
        elif isinstance(block, EmbeddedResource) and isinstance(
            block.resource, TextResourceContents
        ):
            parts.append(block.resource.text)
        else:
            parts.append(f"[{block.type} not shown]")
    if not parts and result.structuredContent is not None:
        parts.append(json.dumps(result.structuredContent))
    return "\n".join(parts) or "(no output)"


@dataclass(frozen=True)
class _Server:
    """How to reach the server; the token is only ever put in the Authorization header."""

    url: str
    settings: McpSettings
    token: SecretStr | None = field(default=None, repr=False)
    http_client_factory: McpHttpClientFactory | None = None

    def kind(self, tool: BaseTool) -> Kind:
        if tool.name in self.settings.act_tools:
            return "act"
        if tool.name in self.settings.read_tools:
            return "marked" if _marks_change(tool) else "read"
        return "not allowed"

    def _client(self) -> MultiServerMCPClient:
        connection = StreamableHttpConnection(
            transport="streamable_http",
            url=self.url,
            timeout=HTTP_TIMEOUT_S,
            sse_read_timeout=CALL_TIMEOUT_S,
        )
        if self.token is not None:
            connection["headers"] = {"Authorization": f"Bearer {self.token.get_secret_value()}"}
        if self.http_client_factory is not None:
            connection["httpx_client_factory"] = self.http_client_factory
        return MultiServerMCPClient({SERVER: connection})

    async def _session[T](
        self, work: Callable[[ClientSession, list[BaseTool]], Awaitable[T]], call_s: float = 0
    ) -> T:
        """One session: list the tools with the adapters, then `work` on that same listing,
        given `call_s` more seconds. Raises `_ServerError`."""
        done: list[T] = []
        try:
            async with asyncio.timeout(LIST_TIMEOUT_S) as deadline:
                async with self._client().session(SERVER) as session:
                    tools = await load_mcp_tools(session)
                    if call_s:
                        deadline.reschedule(asyncio.get_running_loop().time() + call_s)
                    done.append(await work(session, tools))
        except Exception as error:  # transport errors come in many kinds
            raise _ServerError.of(error) from None
        if not done:  # the client can swallow an error when the server drops the session
            raise _ServerError("NoAnswer")
        return done[0]

    async def tools(self) -> list[BaseTool]:
        async def listing(_session: ClientSession, tools: list[BaseTool]) -> list[BaseTool]:
            return tools

        return await self._session(listing)

    async def call(self, name: str, arguments: dict[str, Any], kind: Kind) -> _Answer:
        """Run `name` if the listing has it and it is of `kind` there."""

        async def run(session: ClientSession, tools: list[BaseTool]) -> _Answer:
            tool = next((t for t in tools if t.name == name), None)
            if tool is None:
                return _Answer("missing")
            if self.kind(tool) != kind:
                return _Answer("marked")
            result = await session.call_tool(name, arguments)
            return _Answer("error" if result.isError else "ok", _result_text(result))

        return await self._session(run, CALL_TIMEOUT_S)

    @cached_property
    def _echo(self) -> re.Pattern[str] | None:
        value = self.token.get_secret_value() if self.token is not None else ""
        # Once control and invisible characters are stripped, only whitespace (a space, a line
        # break) can still split an echo. `\s*` cannot match a token character, so a failed
        # match gives up within one gap and the pass stays linear on any input.
        return re.compile(r"\s*".join(re.escape(c) for c in value)) if value else None

    def scrub(self, text: str) -> str:
        """The token out of server text, also where escape codes, control or invisible characters
        or whitespace split it. A transformed echo (base64, reversed, in pieces far apart) is not
        caught."""
        if self._echo is None:
            return text
        return self._echo.sub(REDACTED, strip_control(text))


def _listing(server: _Server, tools: list[BaseTool]) -> str:
    if not tools:
        return "(the server lists no tools)"
    entries = []
    for tool in tools:
        schema = tool.args_schema if isinstance(tool.args_schema, dict) else {}
        shown = json.dumps(schema.get("properties", {}), separators=(",", ":"))[:SCHEMA_CHARS]
        entries.append(
            f"{tool.name} ({_LABEL[server.kind(tool)]})\n  {tool.description or '-'}\n"
            f"  arguments: {shown}"
        )
    return "\n".join(entries)


def _guarded(server: _Server, text: str) -> tuple[str, int]:
    return guard_text("mcp", server.scrub(text))


async def _guard(server: _Server, ctx: ToolContext, text: str) -> str:
    wrapped, redactions = await asyncio.to_thread(_guarded, server, text)
    if redactions and ctx.guard_log is not None:
        ctx.guard_log.record(
            ctx.incident_id, "redacted", f"{redactions} secret(s) redacted from mcp output"
        )
    return wrapped


def _read_tools(server: _Server) -> list[ToolSpec]:
    def unreachable(error: _ServerError) -> str:
        return f"the MCP server could not be reached ({error})"

    def build_list(ctx: ToolContext) -> BaseTool:
        async def run() -> str:
            try:
                return await _guard(server, ctx, _listing(server, await server.tools()))
            except _ServerError as error:
                return unreachable(error)

        return StructuredTool(
            name="mcp_tools", description=TOOLS_DESCRIPTION, args_schema=NoArgs, coroutine=run
        )

    def build_call(ctx: ToolContext) -> BaseTool:
        async def run(tool: str, arguments: dict[str, Any] | None = None) -> str:
            if tool in server.settings.act_tools:
                return IS_ACT.format(tool=tool)
            if tool not in server.settings.read_tools:
                return NOT_ALLOWED.format(tool=tool)
            try:
                answer = await server.call(tool, arguments or {}, "read")
            except _ServerError as error:
                return unreachable(error)
            if answer.status == "missing":
                return NO_SUCH_TOOL
            if answer.status == "marked":
                return MARKED.format(tool=tool)
            guarded = await _guard(server, ctx, answer.text)
            return (
                f"the MCP tool reported an error: {guarded}"
                if answer.status == "error"
                else guarded
            )

        return StructuredTool(
            name="mcp_call",
            description=CALL_DESCRIPTION,
            args_schema=CallArgs,
            coroutine=run,
            handle_validation_error=True,
        )

    return [
        ToolSpec("mcp_tools", "read", NAME, build_list),
        ToolSpec("mcp_call", "read", NAME, build_call),
    ]


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class McpCall:
    """Runs one act tool of the MCP server for a step of an approved plan; Execute alone calls it.

    Before the call it checks, in this order, and refuses with nothing run:

    1. the step is the plan's step at `step_index`, and the grant was issued for this incident
       and for the plan as it is now, its params (tool and arguments) included: else
       `GrantInvalid`;
    2. the target container as the registry sees it now, by the plan check's own rules
       (`check_target`): the host is reachable without a configuration error, and the container
       exists and is neither protected nor Bleep's own;
    3. the params name one of the settings' `act_tools` and a JSON object of arguments, and no
       argument names a protected container or one of Bleep's own on the step's host (by name or
       id): else `StepRefused`.

    What an MCP tool does with its arguments is opaque to Bleep: these checks cover only the
    references it can recognise, and the human who approved the step is the gate for the rest.
    Every refusal is recorded as `blocked_action` in the guard log.
    """

    server: _Server
    secret: SecretStr = field(repr=False)  # BLEEP_SECRET, the grants' key
    guard_log: GuardLogger | None = None
    clock: Callable[[], datetime] = _utcnow

    def check(
        self,
        step: PlanStep,
        grant: ApprovalGrant | None,
        registry: Registry,
        *,
        plan: ApprovalRecord,
        step_index: int,
    ) -> str:
        what = f"step {step_index + 1} ({step.action} {step.target} on {step.host_id})"
        try:
            self._authorize(step, grant, registry, plan, step_index, what)
        except GrantInvalid as refusal:
            self._blocked(plan, f"{what}: refused, {refusal}")
            raise
        except StepRefused as refusal:
            self._blocked(plan, str(refusal))
            raise
        return step.target

    async def run(
        self,
        step: PlanStep,
        grant: ApprovalGrant | None,
        registry: Registry,
        *,
        plan: ApprovalRecord,
        step_index: int,
    ) -> StepResult:
        """Raises `GrantInvalid` or `StepRefused` without calling the server. A failed call is a
        result with `ok=False`, in Bleep's own words: the server's text is not Bleep's."""
        self.check(step, grant, registry, plan=plan, step_index=step_index)
        tool, arguments = _params(step)
        started = self.clock()
        try:
            answer = await self.server.call(tool, arguments, "act")
        except _ServerError as error:
            ok, detail = False, f"the call to the MCP server failed ({error}); it may have run"
        else:
            ok, detail = (
                answer.status == "ok",
                {
                    "ok": f"ran {tool} for {step.target}",
                    "error": "the MCP tool reported an error",
                    "missing": f"the MCP server has no tool {tool}",
                    "marked": f"{tool} is no longer an act tool of the MCP plugin",
                }[answer.status],
            )
        return StepResult(
            step_index=step_index,
            action=ACTION,
            target=step.target,
            ok=ok,
            detail=detail,
            started_at=started,
            finished_at=self.clock(),
        )

    def _blocked(self, plan: ApprovalRecord, text: str) -> None:
        if self.guard_log is not None:
            self.guard_log.record(plan.incident_id, "blocked_action", text)

    def _authorize(
        self,
        step: PlanStep,
        grant: ApprovalGrant | None,
        registry: Registry,
        plan: ApprovalRecord,
        step_index: int,
        what: str,
    ) -> None:
        if step.action != ACTION:
            raise StepRefused(f"{what}: {ACTION} cannot run {step.action}")
        steps = plan.proposal.steps
        if not 0 <= step_index < len(steps):
            raise GrantInvalid(f"the approved plan has no step {step_index + 1}")
        if steps[step_index] != step:
            raise GrantInvalid(f"this is not step {step_index + 1} of the approved plan")
        verify_grant(grant, plan.incident_id, plan_hash(plan.proposal, plan.snapshots), self.secret)

        violations = check_target(step_index + 1, step, registry)
        if violations:
            raise StepRefused("; ".join(v.text for v in violations))
        try:
            tool, arguments = _params(step)
        except ValueError as problem:
            raise StepRefused(f"{what}: {problem}") from None
        if tool not in self.server.settings.act_tools:
            raise StepRefused(f"{what}: {tool} is not one of the MCP plugin's act tools")
        named = _off_limits(arguments, registry, step.host_id)
        if named:
            raise StepRefused(f"{what}: its arguments name {named}")


def _params(step: PlanStep) -> tuple[str, dict[str, Any]]:
    """The tool and its arguments from the step's params; raises `ValueError` in Bleep's words."""
    if not set(step.params) <= {"tool", "arguments"} or "tool" not in step.params:
        raise ValueError("params may hold only tool and arguments, and must name the tool")
    try:
        arguments = json.loads(step.params.get("arguments") or "{}")
    except json.JSONDecodeError:
        arguments = None
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be a JSON object")
    return step.params["tool"], arguments


def _strings(value: Any) -> list[str]:
    """Every string in decoded JSON, keys included, walked with a stack."""
    found: list[str] = []
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            found.append(item)
        elif isinstance(item, dict):
            found += [str(k) for k in item]
            stack += list(item.values())
        elif isinstance(item, list):
            stack += item
    return found


def _off_limits(arguments: dict[str, Any], registry: Registry, host_id: str) -> str | None:
    """Why the arguments may not be sent: they name one of Bleep's own containers on the step's
    host (by name, id or short id) or a protected one. A mention anywhere counts, in any case;
    a container on another host, or one named some other way, is not recognised."""
    host = next((h for h in registry.hosts() if h.config.id == host_id), None)
    protected = protected_names(host) if host is not None else set()
    containers = registry.snapshot(host_id) or []
    own = [c for c in containers if is_self(c.labels)]
    text = "\n".join(_strings(arguments)).lower()
    for c in own:
        refs = {c.name, c.id, c.id[:12]} - {""}
        if any(ref.lower() in text for ref in refs):
            return f"{c.name}, one of Bleep's own containers"
    for name in sorted(protected):
        if name.lower() in text:
            return f"{name}, a container protected in the hosts file"
    return None


class McpUrlPlugin(Plugin):
    """One MCP server over Streamable HTTP, at `MCP_URL`, with `MCP_TOKEN` as its bearer token
    when it is set.

    `http_client_factory` replaces the adapters' own HTTP client; tests pass one that reaches an
    in-process server."""

    name = NAME
    title = "MCP server by URL"
    settings_model = McpSettings
    secrets = (URL_ENV,)
    optional_secrets = (TOKEN_ENV,)

    def __init__(self, http_client_factory: McpHttpClientFactory | None = None) -> None:
        self._http_client_factory = http_client_factory

    def _server(self, ctx: PluginContext) -> _Server:
        settings = ctx.settings
        if not isinstance(settings, McpSettings):
            raise TypeError("the MCP plugin was given another plugin's settings")
        url = str(_URL.validate_python(ctx.secrets[URL_ENV].get_secret_value()))
        return _Server(url, settings, ctx.secrets.get(TOKEN_ENV), self._http_client_factory)

    def tools(self, ctx: PluginContext) -> list[ToolSpec]:
        return _read_tools(self._server(ctx))

    def act_handlers(self, ctx: PluginContext) -> dict[str, ActHandler]:
        server = self._server(ctx)
        if not server.settings.act_tools:
            return {}
        return {ACTION: McpCall(server, ctx.grant_secret, ctx.guard_log)}

    async def health(self, ctx: PluginContext) -> PluginHealth:
        server = self._server(ctx)
        try:
            tools = await server.tools()
        except _ServerError as error:
            return PluginHealth(ok=False, detail=f"the MCP server could not be reached ({error})")
        kinds = {t.name: server.kind(t) for t in tools}
        allowed = [k for k in kinds.values() if k in ("read", "act")]
        detail = (
            f"the server lists {len(tools)} tools; Bleep may run {allowed.count('read')} as read "
            f"and {allowed.count('act')} as act"
        )
        listed = [*server.settings.read_tools, *server.settings.act_tools]
        missing = [n for n in listed if n not in kinds]
        if missing:
            detail += f"; not on the server: {', '.join(missing)}"
        marked = [n for n, k in kinds.items() if k == "marked"]
        if marked:
            detail += f"; refused as read, the server marks them as changing: {', '.join(marked)}"
        return PluginHealth(ok=True, detail=detail)
