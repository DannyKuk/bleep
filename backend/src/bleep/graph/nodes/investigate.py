"""Investigate: the model looks around with read-only tools, within a tool-call budget.

The loop runs inside the node: ask the model, run the tools it calls, hand back the results,
until it answers without a tool call, has made `investigate_tool_calls` calls (8) this round, or
a cost budget is spent. Every tool result reaches the model redacted, cleaned, capped and wrapped
once as untrusted data, whatever the tool did itself. A spent budget sets the outcome `budget`
and tells the human; a spent tool-call allowance only ends the round.
"""

import asyncio
import json
import logging
import re
from collections.abc import Callable
from datetime import datetime
from functools import partial
from typing import Any, Literal

from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage, ToolCall, ToolMessage
from langchain_core.tools import BaseTool

from bleep import prompts
from bleep.graph.deps import GraphDeps
from bleep.graph.host_text import describe_basics, describe_urls, gib, service_name
from bleep.graph.look import look_rule
from bleep.graph.planted import result_holds_tag
from bleep.graph.state import IncidentState, Node
from bleep.guard.log import GuardKind
from bleep.guard.pipeline import guard_text
from bleep.guard.redact import redact_with_count
from bleep.guard.sanitize import MAX_CHARS, strip_control
from bleep.guard.untrusted import SOURCE, wrap_untrusted
from bleep.hosts.shell import CommandNotAllowed
from bleep.llm.usage import BudgetExceeded, UsageTracker, check_budget, tracked
from bleep.memory.text import describe_fact, describe_past
from bleep.schemas.graph import GatherContext
from bleep.schemas.incidents import Signal
from bleep.schemas.settings import Budgets
from bleep.tools.registry import ToolBriefing, ToolContext

log = logging.getLogger(__name__)

MCP_TOOLS_SHOWN = 20  # read tools of the MCP server named in the prompt, at most
SUMMARY_CHARS = 160  # the first line of a result, as the live view shows it
CUT_NOTE_CHARS = 40  # room for the guard's "[… N chars cut]" line
UNWRAP_CHARS = 4 * MAX_CHARS  # longer output is not a guarded one: guard it whole, no regex pass
NOT_RUN = "not run: the tool-call budget for this round is spent"

# One wrapper as `guard.untrusted.wrap_untrusted` writes it. Text from a host has every tag
# escaped, so a match in a tool's output is a wrapper the tool itself put there.
_WRAPPER = re.compile(
    r'<untrusted source="[a-z0-9_.:-]{1,40}" id="([a-z0-9]{4,32})">\n(.*?)\n</untrusted id="\1">',
    re.DOTALL,
)

Record = Callable[[GuardKind, str], None]


def guard_result(source: str, out: str) -> tuple[str, int, bool]:
    """Return the result as the model gets it, the number of secrets redacted, and whether it
    held an escaped wrapper tag, read as the planted-orders hint reads it (`result_holds_tag`).

    A result that is one wrapper already (the Docker and host tools guard their own output) is
    cleaned and redacted again and rewrapped under the tool's name, without a second cut.
    Anything else (a tool's own short text, a wrapper inside other text, a tool that does not
    guard) goes through the whole guard, with the wrappers inside taken off first, so the model
    always sees exactly one. Output too long to be a guarded result skips the unwrapping, so a
    hostile one cannot make the pattern scan it many times; a wrapper in it is escaped as data.
    """
    if len(out) > UNWRAP_CHARS:
        text, redactions = guard_text(source, out)
    elif (whole := _WRAPPER.fullmatch(out)) and len(whole[2]) <= MAX_CHARS + CUT_NOTE_CHARS:
        body, redactions = redact_with_count(strip_control(whole[2]))
        text = wrap_untrusted(source, body)
    else:
        text, redactions = guard_text(source, _WRAPPER.sub(lambda m: m[2], out))
    return text, redactions, result_holds_tag(source, text)


def first_line(guarded: str) -> str:
    """The first non-empty line inside the wrapper, for the live view."""
    body = guarded.split("\n")[1:-1]
    line = next((s.strip() for s in body if s.strip()), "")
    return line if len(line) <= SUMMARY_CHARS else line[: SUMMARY_CHARS - 1] + "…"


def _budget_text(spent: BudgetExceeded, budgets: Budgets) -> str:
    if spent.kind == "daily_cost":
        cap = f"${budgets.daily_cost_usd:.2f}"
        return f"Investigation stopped: the daily model cost cap ({cap}) is spent."
    return (
        f"Investigation stopped: this incident used its {budgets.incident_tokens:,} model tokens."
    )


def _source(name: str) -> str:
    return name if SOURCE.fullmatch(name) else "tool"


def make(deps: GraphDeps) -> Node:
    async def investigate(state: IncidentState) -> dict:
        incident_id = state["incident_id"]
        limit = deps.settings_store.budgets().investigate_tool_calls
        rounds = state.get("investigate_rounds", 0) + 1
        subject = state["subject"]
        ctx = ToolContext(
            incident_id, subject.host_id, deps.registry, deps.guard_log, subject.service_id
        )
        tools = {t.name: t for t in deps.tools.read_tools(ctx)}
        act_tools = {s.name for s in deps.tools.specs() if s.kind == "act"}

        def record(kind: GuardKind, detail: str) -> None:
            if deps.guard_log is not None:
                deps.guard_log.record(incident_id, kind, detail)

        async def run_tool(call: ToolCall) -> ToolMessage:
            name, args = call["name"], call["args"]
            source = _source(name)
            await deps.bus.publish(incident_id, "tool_call", {"name": name, "args": args})
            out = await _invoke(tools.get(name), name, args, act_tools, record)
            text, redactions, marked = await asyncio.to_thread(guard_result, source, out)
            if redactions:
                record("redacted", f"{redactions} secret(s) redacted from {source} output")
            if marked:
                record("injection_marker", f"a wrapper tag in {source} output was escaped")
            await deps.bus.publish(
                incident_id,
                "tool_result",
                {"name": name, "chars": len(text), "summary_first_line": first_line(text)},
            )
            return ToolMessage(text, tool_call_id=call["id"], name=name)

        new = _opening(state, limit, deps.clock, _mcp_section(deps.tools.briefings(ctx)))
        history = [*state.get("messages", []), *new]
        used = 0
        model = None
        tracker = UsageTracker(incident_id, deps.ledger)

        def update(**extra: Any) -> dict:
            return {
                "messages": new,
                "investigate_rounds": rounds,
                "tool_calls_used": state.get("tool_calls_used", 0) + used,
                **extra,
            }

        while used < limit:
            try:
                await check_budget(deps.ledger, incident_id, deps.settings_store.budgets())
            except BudgetExceeded as spent:
                text = _budget_text(spent, deps.settings_store.budgets())
                record("budget", f"{spent.kind} budget spent in investigate round {rounds}")
                await deps.bus.publish(incident_id, "notice", {"text": text})
                try:
                    await deps.notifier.notice(incident_id, text)
                except Exception as error:  # a channel outage must not lose the budget's report
                    log.warning(
                        "investigate: telling %s failed: %s", incident_id, type(error).__name__
                    )
                return update(outcome="budget")
            if model is None:
                model = deps.models("main").bind_tools(list(tools.values()))
            reply = await deps.guard().call(
                partial(model.ainvoke, history, config=tracked(tracker))
            )
            # An invalid call (bad JSON arguments) is not an answer: it needs a reply like any
            # call. One with no id cannot be answered, so it is dropped from what is stored.
            invalid = [c for c in reply.invalid_tool_calls if c.get("id")]
            if len(invalid) != len(reply.invalid_tool_calls):
                reply.invalid_tool_calls = invalid
            new.append(reply)
            history.append(reply)
            if not reply.tool_calls and not invalid:
                break
            for bad in invalid:
                if used < limit:
                    used += 1
                    text = f"invalid arguments: {bad.get('error') or 'could not be parsed'}"
                else:
                    text = NOT_RUN
                message = ToolMessage(text, tool_call_id=bad["id"], name=bad.get("name") or "")
                new.append(message)
                history.append(message)
            for call in reply.tool_calls:
                if used < limit:
                    used += 1
                    message = await run_tool(call)
                else:  # every call needs an answer, or the next model call is refused
                    message = ToolMessage(NOT_RUN, tool_call_id=call["id"], name=call["name"])
                new.append(message)
                history.append(message)
        return update()

    return investigate


async def _invoke(
    tool: BaseTool | None, name: str, args: dict[str, Any], act_tools: set[str], record: Record
) -> str:
    """Run one call and return its text; a refusal or a failure is text for the model too."""
    if tool is None:
        if name in act_tools:
            record("blocked_action", f"{name} asked for while investigating")
            return f"{name} is not available while investigating: only read tools are."
        return f"there is no tool named {name}."
    try:
        out = await tool.ainvoke(args)
    except CommandNotAllowed as refusal:
        record("refused_command", str(args.get("command", args)))
        return str(refusal)
    except Exception as error:
        log.warning("investigate: tool %s failed: %s", name, type(error).__name__)
        return f"the tool failed ({type(error).__name__})."
    return out if isinstance(out, str) else json.dumps(out, default=str)


def _opening(
    state: IncidentState, limit: int, clock: Callable[[], datetime], mcp: str
) -> list[AnyMessage]:
    """The first round starts the conversation; a later one asks again about what was missing."""
    messages = state.get("messages", [])
    if not messages:
        return [
            SystemMessage(prompts.load("graph/system")),
            HumanMessage(_task(state, limit, clock, mcp)),
        ]
    diagnosis = state.get("diagnosis")
    if isinstance(messages[-1], HumanMessage) or diagnosis is None:
        return []  # Diagnose already said what to look for, or there is nothing to add
    again = prompts.load(
        "graph/investigate_again",
        confidence=f"{diagnosis.confidence:.2f}",
        root_cause=diagnosis.root_cause,
        budget=str(limit),
    )
    return [HumanMessage(again)]


def _tool_names(names: tuple[str, ...]) -> str:
    """The first `MCP_TOOLS_SHOWN` read tools by name, and how many more there are: all 50 a
    person may list, at up to 128 characters, would add about 7 KB to every round, and
    `mcp_tools` lists them all."""
    shown = ", ".join(f"`{name}`" for name in names[:MCP_TOOLS_SHOWN])
    left = len(names) - MCP_TOOLS_SHOWN
    return f"{shown}, and {left} more" if left > 0 else shown


def _mcp_section(briefings: list[ToolBriefing]) -> str:
    """What the person wrote about the MCP server and the read tools it may run, set apart from
    the text before it; empty without the plugin, so the prompt is then as it was before #370.
    The purpose is the person's own text, validated when saved, so it goes in as it is."""
    return "".join(
        "\n\n"
        + prompts.load(
            "graph/investigate_mcp",
            purpose=b.purpose,
            tools=_tool_names(b.tools),
        ).strip()
        for b in briefings
    )


def _task(state: IncidentState, limit: int, clock: Callable[[], datetime], mcp: str) -> str:
    subject = state["subject"]
    context = state.get("context")
    if context is None:
        context = GatherContext(at=clock(), gaps=["Gather did not run"])
    if subject.kind == "service":
        what = f"Service {service_name(subject, context)} on host {subject.host_id}."
    else:
        what = f"Host {subject.host_id}."
    return prompts.load(
        "graph/investigate",
        subject=what,
        signals=_signals(state.get("signals", [])),
        look=look_rule("investigate", state.get("signals", [])),
        mcp=mcp,
        containers=_containers(context),
        logs=_logs(context),
        url_checks=_url_checks(context),
        host=_host(context),
        host_rules=_bullets(context.host_rules),
        service_rules=_bullets(context.service_rules),
        similar=_similar(context),
        facts=_facts(context),
        gaps=_bullets(context.gaps),
        budget=str(limit),
        untrusted_rule=prompts.load("untrusted/investigate").strip(),
    )


def _bullets(lines: list[str]) -> str:
    return "\n".join(f"- {line}" for line in lines) or "(none)"


def _signals(signals: list[Signal]) -> str:
    # The details can carry host text (an HTTP error, a log line), so they go in as data.
    lines = [
        f"{s.at.isoformat()} {s.kind}"
        + (f" container={s.container}" if s.container else "")
        + (f" {json.dumps(s.detail, default=str, sort_keys=True)}" if s.detail else "")
        for s in signals
    ]
    return guard_text("signals", "\n".join(lines) or "(none)")[0]


def _containers(context: GatherContext) -> str:
    if not context.containers:
        return "(none)"
    rows = ["name | state | health | image | restarts | exit code"]
    rows += [
        f"{c.name} | {c.state} | {c.health} | {c.image} | {c.restart_count}"
        f" | {c.exit_code if c.exit_code is not None else '-'}"
        for c in context.containers
    ]
    return guard_text("containers", "\n".join(rows))[0]


def _logs(context: GatherContext) -> str:
    # Gather guarded each excerpt already (`guard_text`), so it goes in as it is.
    return "\n\n".join(f"{name}:\n{text}" for name, text in context.logs.items()) or "(none)"


def _url_checks(context: GatherContext) -> str:
    """Gather's URL checks as a section of their own; empty, and the prompt as it was, without."""
    if not context.urls:
        return ""
    return "\n\n" + prompts.load("graph/url_checks", checks=describe_urls(context.urls)).strip()


def _host(context: GatherContext) -> str:
    lines = describe_basics(context.basics) if context.basics is not None else []
    if context.top_memory:
        lines.append("containers using the most memory:")
        lines += [
            f"  {m.name}: {gib(m.mem_usage)}"
            + (f" of {gib(m.mem_limit)}" if m.mem_limit else "")
            + f" ({m.mem_percent:.0f}%)"
            for m in context.top_memory
        ]
    return "\n".join(lines) or "(no basics read)"


def _similar(context: GatherContext) -> str:
    return _bullets([describe_past(p) for p in context.similar])


def _facts(context: GatherContext) -> str:
    return _bullets([describe_fact(f) for f in context.facts])


def route(state: IncidentState) -> Literal["diagnose", "report"]:
    """A spent budget ends the run with a report; otherwise diagnose what was found."""
    return "report" if state.get("outcome") == "budget" else "diagnose"
