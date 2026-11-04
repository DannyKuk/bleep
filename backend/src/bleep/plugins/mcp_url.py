"""The MCP server by URL plugin: one MCP server's tools, through LangChain's MCP adapters.

The server's endpoint and token come from the environment (`MCP_URL`, `MCP_TOKEN`), never from
the stored settings, which only say which of its tools Bleep may run: `read_tools` without
approval, `act_tools` only as the `mcp.call` step of a plan a human approved. Any other tool is
not allowed. The model gets two of Bleep's own tools rather than the server's: `mcp_tools` lists
what the server offers (its names, descriptions and input schemas, wrapped as untrusted data) and
`mcp_call` runs a read tool. Each call opens one session, lists the tools with the adapters and
calls the tool on that same session directly, so no LangChain runnable of the adapters runs under
Bleep's trace. `MCP_URL` follows the same rule as `COOLIFY_URL` (`plugins/urls.check_url`), and
every answer is read uncompressed and capped on its raw bytes (`_CappedTransport`). See
docs/design/plugins.md.
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

import httpx
from langchain_core.tools import BaseTool, StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import StreamableHttpConnection
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession
from mcp.types import CallToolResult, EmbeddedResource, TextContent, TextResourceContents
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    model_validator,
)

from bleep.guard.grant import GrantInvalid, plan_hash, verify_grant
from bleep.guard.log import GuardLogger
from bleep.guard.pipeline import guard_text
from bleep.guard.plan_check import (
    Violation,
    check_arguments,
    check_params,
    check_target,
    mcp_arguments,
    named_off_limits,
)
from bleep.guard.redact import REDACTED
from bleep.guard.sanitize import INVISIBLE_CHARS, MAX_CHARS, strip_control
from bleep.guard.untrusted import escape_untrusted
from bleep.hosts.registry import Registry
from bleep.plugins import urls
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
DESCRIPTION_CHARS = 400  # a tool's description in the listing, at most
REQUIRED_CHARS = 200  # the names of a tool's required arguments, at most
# The whole listing, measured as the guard will escape it, so `guard_text` (which keeps the tail
# past MAX_CHARS) never cuts into it; the margin covers what redaction can add.
LISTING_CHARS = MAX_CHARS - 1000
MAX_BYTES = 2_000_000  # of one HTTP answer, counted before any decompression

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
OWN = "the arguments name {named}; Bleep does not call MCP tools about itself"
_LABEL: dict[Kind, str] = {
    "read": "read, mcp_call runs it",
    "act": "act, runs only as an approved mcp.call step",
    "marked": "not allowed: listed as read, but the server marks it as changing something",
    "not allowed": "not allowed: not one of the plugin's read or act tools",
}


class McpUrlRefused(urls.UrlRefused):
    """`MCP_URL` is not one Bleep sends the token to; the text never quotes it."""


class CompressedAnswer(httpx.TransportError):
    """The server sent a compressed answer, which could inflate past the cap before any count."""


class AnswerTooLarge(httpx.TransportError):
    """An answer ran past `MAX_BYTES` of raw bytes; Bleep stopped reading it."""


class UnreadableAnswer(httpx.TransportError):
    """The transport under the cap gave an answer it cannot stream; a wiring fault, not the
    server's."""


class _CappedStream(httpx.AsyncByteStream):
    def __init__(self, inner: httpx.AsyncByteStream, limit: int) -> None:
        self._inner, self._limit = inner, limit

    async def __aiter__(self):
        seen = 0
        async for chunk in self._inner:
            seen += len(chunk)
            if seen > self._limit:
                raise AnswerTooLarge(f"the MCP server's answer is over {self._limit} bytes")
            yield chunk

    async def aclose(self) -> None:
        await self._inner.aclose()


class _CappedTransport(httpx.AsyncBaseTransport):
    """Every answer as the server sent it, refused when compressed and cut off past `limit` raw
    bytes. httpx decodes a body only above the transport, so the count here comes before any
    inflation.

    The answer to a POST (every request Bleep makes) is read whole here, so going over the cap
    fails the request at once: the MCP client drops an error raised while it reads a body and
    waits for an answer that never comes. A GET is the server's own event stream, open for the
    session, so it stays a stream and is only cut off at the cap."""

    def __init__(self, inner: httpx.AsyncBaseTransport, limit: int) -> None:
        self._inner, self._limit = inner, limit

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        encoding = response.headers.get("content-encoding", "identity").strip().lower()
        if encoding not in ("", "identity"):
            await response.aclose()
            raise CompressedAnswer("the MCP server sent a compressed answer", request=request)
        if not isinstance(response.stream, httpx.AsyncByteStream):
            if isinstance(response.stream, httpx.SyncByteStream):
                response.stream.close()
            raise UnreadableAnswer("the transport gave an answer that cannot be read async")
        stream = _CappedStream(response.stream, self._limit)
        if request.method != "POST":
            return httpx.Response(
                status_code=response.status_code,
                headers=response.headers,
                stream=stream,
                extensions=response.extensions,
                request=request,
            )
        try:
            body = b"".join([chunk async for chunk in stream])
        finally:
            await stream.aclose()
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            content=body,
            extensions=response.extensions,
            request=request,
        )

    async def aclose(self) -> None:
        await self._inner.aclose()


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
    transport: httpx.AsyncBaseTransport | None = None

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
        connection["httpx_client_factory"] = self._http_client
        return MultiServerMCPClient({SERVER: connection})

    def _http_client(
        self,
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        """The adapters' HTTP client (`mcp.shared._httpx_utils.create_mcp_http_client`), asking
        for plain answers and reading them through `_CappedTransport`."""
        return httpx.AsyncClient(
            headers={**(headers or {}), "Accept-Encoding": "identity"},
            timeout=timeout or HTTP_TIMEOUT_S,
            auth=auth,
            transport=_CappedTransport(self.transport or httpx.AsyncHTTPTransport(), MAX_BYTES),
        )

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
    def _echoes(self) -> tuple[re.Pattern[str], re.Pattern[str]] | None:
        value = self.token.get_secret_value() if self.token is not None else ""
        # Whitespace in the token itself is left out: a gap pattern that could match a token
        # character lets one failed match retry inside every gap, quadratic again.
        chars = [re.escape(c) for c in value if not c.isspace()]
        if not chars:
            return None
        # Each gap matches only characters a token never holds, so a failed match gives up
        # within one gap and both passes stay linear on any input. The raw pass catches an
        # escape whose stripping would also take a token character with it (ESC + letter); the
        # stripped pass catches a split by a longer escape sequence.
        raw_gap = rf"(?:[\s\x00-\x1f\x7f-\x9f]|{INVISIBLE_CHARS})*"
        return re.compile(raw_gap.join(chars)), re.compile(r"\s*".join(chars))

    def scrub(self, text: str) -> str:
        """The token out of server text, also where escape codes, control or invisible characters
        or whitespace split it. A transformed echo (base64, reversed, in pieces far apart) is not
        caught."""
        if self._echoes is None:
            return text
        raw, stripped = self._echoes
        return stripped.sub(REDACTED, strip_control(raw.sub(REDACTED, text)))


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _entry(server: _Server, tool: BaseTool) -> str:
    """One tool of the listing: its name and label first, then its description, its input
    schema's properties and its required arguments, each cut to its own cap."""
    schema = tool.args_schema if isinstance(tool.args_schema, dict) else {}
    shown = json.dumps(schema.get("properties", {}), separators=(",", ":"))[:SCHEMA_CHARS]
    required = schema.get("required")
    names = [str(r) for r in required] if isinstance(required, list) else []
    return (
        f"{tool.name} ({_LABEL[server.kind(tool)]})\n"
        f"  {_cut(' '.join((tool.description or '-').split()), DESCRIPTION_CHARS)}\n"
        f"  arguments: {shown}\n"
        f"  required: {_cut(', '.join(names), REQUIRED_CHARS) or 'none'}"
    )


def _listing(server: _Server, tools: list[BaseTool]) -> str:
    """Whole entries only, up to `LISTING_CHARS`, so no schema is left without its tool's
    name; the tools that do not fit are counted at the end."""
    if not tools:
        return "(the server lists no tools)"
    entries: list[str] = []
    used = 0
    for tool in tools:
        entry = _entry(server, tool)
        size = len(escape_untrusted(entry)) + 1
        if used + size > LISTING_CHARS:
            break
        entries.append(entry)
        used += size
    left = len(tools) - len(entries)
    if left:
        entries.append(f"({left} more tools not shown: the listing is full)")
    return "\n".join(entries)


def _guarded(server: _Server, text: str) -> tuple[str, int]:
    return guard_text("mcp", server.scrub(text))


async def _guard(
    server: _Server, guard_log: GuardLogger | None, incident_id: str, text: str
) -> str:
    wrapped, redactions = await asyncio.to_thread(_guarded, server, text)
    if redactions and guard_log is not None:
        detail = f"{redactions} secret(s) redacted from mcp output"
        guard_log.record(incident_id, "redacted", detail)
    return wrapped


def _read_tools(server: _Server) -> list[ToolSpec]:
    def unreachable(error: _ServerError) -> str:
        return f"the MCP server could not be reached ({error})"

    def build_list(ctx: ToolContext) -> BaseTool:
        async def run() -> str:
            try:
                listing = _listing(server, await server.tools())
                return await _guard(server, ctx.guard_log, ctx.incident_id, listing)
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
            own = named_off_limits(arguments or {}, ctx.registry, ctx.host_id, protected=False)
            if own is not None:
                return OWN.format(named=own[1])
            try:
                answer = await server.call(tool, arguments or {}, "read")
            except _ServerError as error:
                return unreachable(error)
            if answer.status == "missing":
                return NO_SUCH_TOOL
            if answer.status == "marked":
                return MARKED.format(tool=tool)
            guarded = await _guard(server, ctx.guard_log, ctx.incident_id, answer.text)
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
    3. the params are exactly a tool and a JSON object of arguments (`check_params`), the tool
       is one of the settings' `act_tools` (`plan_violations`, which the plan check runs too), and
       no argument names a protected container or one of Bleep's own on the step's host, by name
       or id (`check_arguments`): else `StepRefused`.

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

    def plan_violations(self, number: int, step: PlanStep) -> list[Violation]:
        """The plugin's own rule for an `mcp.call` step, which the plan check runs before a
        human is asked: its tool is one of the settings' `act_tools`."""
        tool = step.params.get("tool", "")
        if tool in self.server.settings.act_tools:
            return []
        what = f"step {number} ({step.action} {step.target} on {step.host_id})"
        why = f"{tool} is not one of the MCP plugin's act tools"
        return [Violation("bad_params", f"{what}: {why}", number)]

    def act_tool_names(self) -> list[str]:
        return list(self.server.settings.act_tools)

    async def act_tool_schemas(self, incident_id: str) -> str:
        """The act tools as the server lists them now (description, input schema), wrapped as
        untrusted data; a redaction in it is logged for `incident_id`. When the server cannot
        be reached, or lists none of them, it says so in Bleep's own words."""
        try:
            tools = await self.server.tools()
        except _ServerError as error:
            return f"not known: the MCP server could not be reached ({error})"
        acts = [t for t in tools if self.server.kind(t) == "act"]
        if not acts:
            return "not known: the MCP server lists none of these tools now"
        return await _guard(self.server, self.guard_log, incident_id, _listing(self.server, acts))

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

        number = step_index + 1
        violations = (
            check_target(number, step, registry)
            or check_params(number, step)
            or self.plan_violations(number, step)
            or check_arguments(number, step, registry)
        )
        if violations:
            raise StepRefused("; ".join(v.text for v in violations))


def _params(step: PlanStep) -> tuple[str, dict[str, Any]]:
    """The tool and its arguments of a step `McpCall.check` passed."""
    return step.params["tool"], mcp_arguments(step) or {}


class McpUrlPlugin(Plugin):
    """One MCP server over Streamable HTTP, at `MCP_URL`, with `MCP_TOKEN` as its bearer token
    when it is set.

    `transport` is the one the HTTP client sends through, under the cap; tests pass one that
    reaches an in-process server."""

    name = NAME
    title = "MCP server by URL"
    settings_model = McpSettings
    secrets = (URL_ENV,)
    optional_secrets = (TOKEN_ENV,)

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def _server(self, ctx: PluginContext) -> _Server:
        """Raises `McpUrlRefused` for an `MCP_URL` Bleep must not send the token to; the
        registry then refuses to switch the plugin on, naming that type only."""
        settings = ctx.settings
        if not isinstance(settings, McpSettings):
            raise TypeError("the MCP plugin was given another plugin's settings")
        try:
            url = urls.check_url(
                ctx.secrets[URL_ENV].get_secret_value(),
                URL_ENV,
                McpUrlRefused,
                https_hint="; use the MCP server's https endpoint",
            )
        except McpUrlRefused as refusal:
            log.warning("mcp: %s", refusal)
            raise
        return _Server(url, settings, ctx.secrets.get(TOKEN_ENV), self._transport)

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
