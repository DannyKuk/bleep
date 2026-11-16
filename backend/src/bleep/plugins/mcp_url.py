"""The MCP server by URL plugin: one MCP server's tools, through LangChain's MCP adapters.

The server's endpoint and token (`MCP_URL`, `MCP_TOKEN`) come from the environment or the
dashboard's secret file, never from the stored settings. They say what the server is for (`purpose`,
one line the person writes, which Investigate's prompt names) and which of its tools Bleep may run:
`read_tools` without approval, `act_tools` only as the `mcp.call` step of a plan a human approved.
Any other tool is not allowed. The model gets two of Bleep's own tools rather than the
server's: `mcp_tools` lists what the server offers (its names, descriptions and input schemas,
wrapped as untrusted data) and `mcp_call` runs a read tool. Each call opens one session, lists the
tools with the adapters and calls the tool on that same session directly, so no LangChain runnable
of the adapters runs under Bleep's trace. `MCP_URL` follows the same rule as `COOLIFY_URL`
(`plugins/urls.check_url`), and every answer is read uncompressed and capped on its raw bytes
(`_CappedTransport`). See docs/design/plugins.md.
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
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    model_validator,
)
from pydantic_core import PydanticCustomError

from bleep.guard.grant import GrantInvalid, check_step_index, plan_hash, verify_grant
from bleep.guard.log import GuardLogger
from bleep.guard.pipeline import guard_text
from bleep.guard.plan_check import (
    Violation,
    check_arguments,
    check_params,
    check_shown,
    check_target,
    mcp_arguments,
    named_off_limits,
    step_violation,
)
from bleep.guard.redact import REDACTED
from bleep.guard.sanitize import INVISIBLE_CHARS, MAX_CHARS, strip_control
from bleep.guard.untrusted import escape_untrusted
from bleep.hosts.registry import Registry
from bleep.plugins import urls
from bleep.plugins.base import Plugin, PluginContext
from bleep.schemas.graph import ApprovalGrant, ApprovalRecord, PlanStep, StepResult
from bleep.schemas.plugins import PluginHealth
from bleep.schemas.shown import Shown
from bleep.tools.common import ArgText, described, say, untrusted_rule
from bleep.tools.docker_act import StepRefused
from bleep.tools.registry import ActHandler, ToolBriefing, ToolContext, ToolSpec

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
NAME_CHARS = 120  # a tool's name in the listing, at most; the server's tools may have any name
REQUIRED_CHARS = 200  # the names of a tool's required arguments, at most
# The whole listing, measured as the guard will escape it, so `guard_text` (which keeps the tail
# past MAX_CHARS) never cuts into it; the margin covers what redaction can add.
LISTING_CHARS = MAX_CHARS - 1000
MAX_BYTES = 2_000_000  # of one HTTP answer, counted before any decompression

PURPOSE_CHARS = 200  # one sentence on what the server is for; it is read on every Investigate round
_NOT_PLAIN = re.compile(rf"[\x00-\x1f\x7f-\x9f\u2028\u2029]|{INVISIBLE_CHARS}")


def _one_plain_line(text: str) -> str:
    if _NOT_PLAIN.search(text):
        raise PydanticCustomError(
            "plain_text_line",
            "Value error, must be one line of plain text, without control characters",
        )
    return text


ToolName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.\-/]{1,128}$")]
Purpose = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=PURPOSE_CHARS),
    AfterValidator(_one_plain_line),
]
Kind = Literal["read", "act", "marked", "not allowed"]


class McpSettings(BaseModel):
    """Which of the server's tools Bleep may run, and what the server is for; the endpoint and
    token are in the env."""

    model_config = ConfigDict(extra="forbid")

    purpose: Purpose = Field(
        default="",
        examples=["Manages the Coolify server: apps, deployments, env settings"],
        description=(
            "What the server is for, in a line: Investigate tells the model, so it knows when"
            " asking the server is worth a call. Your own words, not the server's. Only used once"
            " read tools are listed: with none, Investigate is not told."
        ),
    )

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
            raise PydanticCustomError(
                "tool_read_and_act", "Value error, a tool is either read or act, not both"
            )
        return self


class CallArgs(BaseModel):
    tool: Annotated[str, ArgText("plugins/args/tool")]
    arguments: Annotated[dict[str, Any], ArgText("plugins/args/arguments")] = Field(
        default_factory=dict
    )


class NoArgs(BaseModel):
    pass


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
        raw_gap = rf"(?:[\s\x00-\x1f\x7f-\x9f\ud800-\udfff]|{INVISIBLE_CHARS})*"
        return re.compile(raw_gap.join(chars)), re.compile(r"\s*".join(chars))

    def scrub(self, text: str) -> str:
        """The token out of server text, also where escape codes, control or invisible characters,
        lone surrogates or whitespace split it. A transformed echo (base64, reversed, in pieces far
        apart) is not caught."""
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
    if required is None or isinstance(required, list):
        names = _cut(", ".join(str(r) for r in required or []), REQUIRED_CHARS) or "none"
    else:
        names = "(not a list)"
    return (
        f"{_cut(tool.name, NAME_CHARS)} ({_LABEL[server.kind(tool)]})\n"
        f"  {_cut(' '.join((tool.description or '-').split()), DESCRIPTION_CHARS)}\n"
        f"  arguments: {shown}\n"
        f"  required: {names}"
    )


def _size(text: str) -> int:
    """What a listing line takes of `LISTING_CHARS`: escaped as the guard will, plus its break."""
    return len(escape_untrusted(text)) + 1


def _fit(sizes: list[int], budget: int) -> list[bool]:
    """Which entries go in, in order: one that does not fit is skipped, a later smaller one may
    still go in."""
    used = 0
    fits = []
    for size in sizes:
        fit = used + size <= budget
        if fit:
            used += size
        fits.append(fit)
    return fits


def _count_line(count: int) -> str:
    word = "tool" if count == 1 else "tools"
    return f"({count} more {word} not shown: the listing is full)"


def _allowed_left_line(names: list[str], room: int) -> str:
    """Every allowed tool left out, by its whole name, so the model can still call it. Only when
    the names alone would not fit `room` are whole names dropped from the end and counted.
    One pass over the names, with running lengths."""
    word = "tool" if len(names) == 1 else "tools"
    head = f"({len(names)} allowed {word} not shown: the listing is full; "
    base = _size(head + ")")
    sizes = [len(escape_untrusted(name)) + 2 for name in names]  # each with its ", "
    if base + sum(sizes) - 2 <= room:
        return head + ", ".join(names) + ")"
    reserve = len(f"and {len(names)} more")
    shown = 0
    used = base + reserve
    for size in sizes:
        if used + size > room:
            break
        used += size
        shown += 1
    return head + ", ".join([*names[:shown], f"and {len(names) - shown} more"]) + ")"


def _distinct(tools: list[BaseTool]) -> list[BaseTool]:
    """The first tool of each name: a server can list the same name again and again, and a
    second entry says nothing more about it."""
    first: dict[str, BaseTool] = {}
    for tool in tools:
        first.setdefault(tool.name, tool)
    return list(first.values())


def _listing(server: _Server, tools: list[BaseTool], *, name_allowed_left: bool = True) -> str:
    """Whole entries only, up to `LISTING_CHARS`, so no schema is left without its tool's name.
    The tools Bleep may run (read and act) go first, in the server's order, then the others fill
    what is left. When something does not fit, the closing lines are paid for before any entry
    that is not allowed: the allowed tools left out by name (`name_allowed_left`) and a count of
    the others. Without the names, every tool left out is only counted."""
    if not tools:
        return "(the server lists no tools)"
    tools = _distinct(tools)
    allowed = [t for t in tools if server.kind(t) in ("read", "act")]
    rest = [t for t in tools if server.kind(t) not in ("read", "act")]
    entries = [_entry(server, t) for t in (*allowed, *rest)]
    sizes = [_size(e) for e in entries]
    if sum(sizes) <= LISTING_CHARS:
        return "\n".join(entries)

    if not name_allowed_left:
        fits = _fit(sizes, LISTING_CHARS - _size(_count_line(len(entries))))
        shown = [e for e, fit in zip(entries, fits, strict=True) if fit]
        return "\n".join([*shown, _count_line(fits.count(False))])

    count_cost = _size(_count_line(len(rest))) if rest else 0
    n = len(allowed)
    names_cost = 0
    while True:
        fits = _fit(sizes[:n], LISTING_CHARS - count_cost - names_cost)
        left = [t.name for t, fit in zip(allowed, fits, strict=True) if not fit]
        names_line = _allowed_left_line(left, LISTING_CHARS - count_cost) if left else ""
        cost = _size(names_line) if names_line else 0
        if cost <= names_cost:
            break
        names_cost = cost

    room = LISTING_CHARS - cost - sum(s for s, fit in zip(sizes[:n], fits, strict=True) if fit)
    rest_fits = _fit(sizes[n:], room)
    if not all(rest_fits):
        rest_fits = _fit(sizes[n:], room - count_cost)
    shown = [e for e, fit in zip(entries, [*fits, *rest_fits], strict=True) if fit]
    if names_line:
        shown.append(names_line)
    if not all(rest_fits):
        shown.append(_count_line(rest_fits.count(False)))
    return "\n".join(shown)


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
    def unreachable(ctx: ToolContext, error: _ServerError) -> str:
        return say(ctx, "plugins/reply/mcp_unreachable", error=str(error))

    def build_list(ctx: ToolContext) -> BaseTool:
        async def run() -> str:
            try:
                listing = _listing(server, await server.tools())
                return await _guard(server, ctx.guard_log, ctx.incident_id, listing)
            except _ServerError as error:
                return unreachable(ctx, error)

        return StructuredTool(
            name="mcp_tools",
            description=say(ctx, "plugins/mcp_tools", untrusted_rule=untrusted_rule(ctx)),
            args_schema=described(ctx, NoArgs),
            coroutine=run,
        )

    def build_call(ctx: ToolContext) -> BaseTool:
        async def run(tool: str, arguments: dict[str, Any] | None = None) -> str:
            if tool in server.settings.act_tools:
                return say(ctx, "plugins/reply/mcp_is_act", tool=tool)
            if tool not in server.settings.read_tools:
                return say(ctx, "plugins/reply/mcp_not_allowed", tool=tool)
            own = named_off_limits(arguments or {}, ctx.registry, ctx.host_id, protected=False)
            if own is not None:
                return say(ctx, "plugins/reply/mcp_own", named=own[1])
            try:
                answer = await server.call(tool, arguments or {}, "read")
            except _ServerError as error:
                return unreachable(ctx, error)
            if answer.status == "missing":
                return say(ctx, "plugins/reply/mcp_no_such_tool")
            if answer.status == "marked":
                return say(ctx, "plugins/reply/mcp_marked", tool=tool)
            guarded = await _guard(server, ctx.guard_log, ctx.incident_id, answer.text)
            if answer.status == "error":
                return say(ctx, "plugins/reply/mcp_tool_error", output=guarded)
            return guarded

        return StructuredTool(
            name="mcp_call",
            description=say(ctx, "plugins/mcp_call", untrusted_rule=untrusted_rule(ctx)),
            args_schema=described(ctx, CallArgs),
            coroutine=run,
            handle_validation_error=True,
        )

    settings = server.settings
    briefing = (
        ToolBriefing(settings.purpose, tuple(settings.read_tools))
        if settings.purpose and settings.read_tools
        else None
    )
    return [
        ToolSpec("mcp_tools", "read", NAME, build_list, briefing=briefing),
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
        why = f"{tool} is not one of the MCP plugin's act tools"
        shown = Shown.of("refusal/not_act_tool", tool=tool)
        return [step_violation("bad_params", number, step, why, shown)]

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
        return await _guard(
            self.server,
            self.guard_log,
            incident_id,
            _listing(self.server, acts, name_allowed_left=False),
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
            why = f"{ACTION} cannot run {step.action}"
            shown = Shown.of("refusal/cannot_run", tool=ACTION, action=step.action)
            raise StepRefused.at(step_index, step, why, shown)
        check_step_index(step, plan, step_index)
        verify_grant(grant, plan.incident_id, plan_hash(plan.proposal, plan.snapshots), self.secret)

        number = step_index + 1
        violations = (
            check_target(number, step, registry)
            or check_params(number, step)
            or self.plan_violations(number, step)
            or check_arguments(number, step, registry)
            or check_shown(number, step)
        )
        if violations:
            raise StepRefused.of(violations)


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
    experimental = True
    settings_model = McpSettings
    secrets = (URL_ENV,)
    optional_secrets = (TOKEN_ENV,)
    connection = (URL_ENV, TOKEN_ENV)

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
                hint_name="mcp",
            )
        except McpUrlRefused as refusal:
            log.warning("mcp: %s", refusal)
            raise
        transport = urls.guarded(self._transport, ctx.public_only)
        return _Server(url, settings, ctx.secrets.get(TOKEN_ENV), transport)

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
