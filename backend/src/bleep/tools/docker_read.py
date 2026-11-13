"""Read-only Docker tools for the Investigate step.

Everything they return came from a container or the engine, so it is untrusted: each result is
redacted, cleaned, cut to a size budget and wrapped in `<untrusted>` (see `guard.pipeline`).
Every tool is scoped to the incident's host and cannot see another host.
"""

import json
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from bleep.guard.redact import (
    REDACTED,
    hide_values,
    redact_command,
    redact_env,
    redact_text,
    secret_env_values,
)
from bleep.hosts.discovery import is_self
from bleep.hosts.engine import ContainerNotFound, EngineError, EngineUnavailable, LogClock
from bleep.schemas.docker import (
    ContainerDetail,
    ContainerSummary,
    DockerEvent,
    HealthCheck,
    HealthProbe,
)
from bleep.tools.common import UNTRUSTED, guard_result, invalid_arguments
from bleep.tools.registry import ToolContext, ToolSpec

NOT_FOUND = "not found on this host"
LABEL_CHARS = 80
PROBE_OUTPUT_CHARS = 200
HEALTH_COMMAND_CHARS = 300
_SECRET_LABEL = re.compile(r"auth|password|passwd|secret|token|key|users", re.IGNORECASE)


class NoArgs(BaseModel):
    pass


class ContainerArgs(BaseModel):
    container: str = Field(description="Container name or id, as shown by list_containers.")


class LogsArgs(ContainerArgs):
    tail: int = Field(200, ge=1, le=1000, description="How many of the last lines to read.")
    since_minutes: int | None = Field(
        None, ge=1, le=1440, description="Only lines from the last N minutes (max 1440)."
    )


class EventsArgs(BaseModel):
    minutes: int = Field(30, ge=1, le=240, description="How far back to look (max 240).")


def _tool(
    ctx: ToolContext,
    name: str,
    description: str,
    schema: type[BaseModel],
    run: Callable[..., Awaitable[str]],
) -> StructuredTool:
    async def call(**args) -> str:
        try:
            return await run(ctx, **args)
        except EngineUnavailable as error:
            return f"host unreachable: {await guard_result(ctx, 'error', str(error))}"
        except ContainerNotFound:
            return NOT_FOUND
        except EngineError as error:
            return f"docker error: {await guard_result(ctx, 'error', str(error))}"

    return StructuredTool(
        name=name,
        description=description,
        args_schema=schema,
        coroutine=call,
        handle_validation_error=invalid_arguments,
    )


def _find(ctx: ToolContext, ref: str) -> ContainerSummary | None:
    """The container on this host, or None; Bleep's own containers are never found."""
    found = ctx.registry.container(ctx.host_id, ref)
    return None if found is not None and _own(found) else found


def _own(container: ContainerSummary) -> bool:
    return is_self(container.labels)


def _own_event(ctx: ToolContext, event: DockerEvent) -> bool:
    """Docker labels every event of a Bleep container; the listing covers events without labels."""
    if is_self(event.attributes):
        return True
    ref = event.container_id or event.container_name
    known = ctx.registry.container(ctx.host_id, ref) if ref else None
    if known is None and event.container_name:
        known = ctx.registry.container(ctx.host_id, event.container_name)
    return known is not None and _own(known)


def _since(delta: timedelta) -> datetime:
    return datetime.now(UTC) - delta


def _size(n: int | None) -> str:
    if not n:
        return "none"
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def _when(value: datetime | None) -> str:
    return value.isoformat() if value else "-"


async def _list(ctx: ToolContext) -> str:
    services = {c.name: s.name for s in ctx.registry.services(ctx.host_id) for c in s.containers}
    rows = ["name | service | state | health | image | restarts"]
    for c in await ctx.registry.engine(ctx.host_id).containers():
        if _own(c):
            continue
        rows.append(
            f"{c.name} | {services.get(c.name, '-')} | {c.state} | {c.health} | {c.image}"
            f" | {c.restart_count}"
        )
    return await guard_result(ctx, "containers", "\n".join(rows))


def _describe(detail: ContainerDetail) -> str:
    c = detail.summary
    lines = [
        f"name: {c.name}",
        f"image: {c.image}",
        f"state: {c.state}",
        f"health: {c.health}",
        *_health_lines(detail),
        f"restart policy: {c.restart_policy or 'none'}",
        f"restart count: {detail.restart_count}",
        f"exit code: {c.exit_code if c.exit_code is not None else '-'}",
        f"OOMKilled: {str(detail.oom_killed).lower()}",
        f"started_at: {_when(c.started_at)}",
        f"finished_at: {_when(c.finished_at)}",
        f"mem limit: {_size(detail.mem_limit)}",
    ]
    if detail.error:
        lines.append(f"engine error: {detail.error}")
    lines.append("ports: " + (", ".join(_port(p) for p in detail.ports) or "none"))
    lines.append("mounts:" + ("" if detail.mounts else " none"))
    lines += [
        f"  {m.type} {m.source} -> {m.destination}{' (ro)' if m.read_only else ''}"
        for m in detail.mounts
    ]
    lines.append("labels:" + ("" if c.labels else " none"))
    for key, value in sorted(c.labels.items()):
        shown = REDACTED if _SECRET_LABEL.search(key) else redact_text(value)
        if len(shown) > LABEL_CHARS:
            shown = shown[:LABEL_CHARS] + "…"
        lines.append(f"  {key}={shown}")
    lines.append(
        "env (a plain value is shown; a secret or token-like value is hidden, and a service URL"
        " keeps scheme, host and port, so a bare <redacted> may still be a URL):"
        + ("" if detail.env else " none")
    )
    lines += [f"  {item}" for item in redact_env(detail.env)]
    return "\n".join(lines)


def _command(check: HealthCheck, hidden: list[str]) -> str:
    """The check's command on one line, secrets out before the cut so a cut cannot leave half a
    token behind. Compose copies an env value into a command (`redis-cli -a $REDIS_PASSWORD`), so
    the values the env lines hide are hidden here too, word by word, then the usual redaction."""
    words = [hide_values(word, hidden) for word in check.command]
    if check.shell:
        head, body = "health check, run by a shell", " ".join(words)
    else:
        head, body = "health check, run without a shell", json.dumps(words, ensure_ascii=False)
    line = " ".join(redact_command(body).split())
    if len(line) > HEALTH_COMMAND_CHARS:
        line = line[:HEALTH_COMMAND_CHARS] + "…"
    return f"{head}: {line}"


def _probe_output(text: str, limit: int, hidden: list[str]) -> str:
    """One line of at most about `limit` characters that keeps the start and the last line.

    A failed probe's output is often a traceback whose last line names the error, so a plain
    cut at the start would drop the one line that matters. Redaction runs before the cut.
    """
    lines = [" ".join(line.split()) for line in redact_text(hide_values(text, hidden)).splitlines()]
    lines = [line for line in lines if line]
    flat = " ".join(lines)
    if len(flat) <= limit:
        return flat
    if len(lines) == 1:
        return f"{flat[: limit // 2]} … {flat[-(limit // 2) :]}"
    last = lines[-1] if len(lines[-1]) <= limit // 2 else lines[-1][: limit // 2] + "…"
    return f"{flat[: limit - len(last) - 3]} … {last}"


def _seconds(value: float | None) -> str:
    return "default" if value is None else f"{value:g}s"


def _check(check: HealthCheck, hidden: list[str]) -> str:
    retries = "default" if check.retries is None else str(check.retries)
    return (
        f"{_command(check, hidden)} (interval {_seconds(check.interval_s)}, "
        f"timeout {_seconds(check.timeout_s)}, retries {retries}, "
        f"start period {_seconds(check.start_period_s)})"
    )


def _probe(probe: HealthProbe, hidden: list[str]) -> str:
    took = ""
    if probe.start and probe.end:
        took = f" took {(probe.end - probe.start).total_seconds():.1f}s,"
    output = _probe_output(probe.output, PROBE_OUTPUT_CHARS, hidden) or "(no output)"
    code = "-" if probe.exit_code is None else probe.exit_code
    return f"  {_when(probe.start)}{took} exit {code}: {output}"


def _health_lines(detail: ContainerDetail) -> list[str]:
    """Nothing when the engine gave no health detail for a container that has a health state, so
    a fake or partial inspect never says the probes are unavailable."""
    hidden = secret_env_values(detail.env)
    if detail.healthcheck is None and not detail.probes:
        return ["health check: none configured"] if detail.summary.health == "none" else []
    lines = [_check(detail.healthcheck, hidden)] if detail.healthcheck else []
    if detail.probes:
        return lines + ["last probes (oldest first):", *[_probe(p, hidden) for p in detail.probes]]
    return lines + (
        ["last probes: none recorded yet"] if detail.summary.health == "starting" else []
    )


def _port(port) -> str:
    if port.host_port is None:
        return f"{port.container_port} (not published)"
    return f"{port.host_ip or '*'}:{port.host_port} -> {port.container_port}"


async def _inspect(ctx: ToolContext, container: str) -> str:
    found = _find(ctx, container)
    if found is None:
        return NOT_FOUND
    detail = await ctx.registry.engine(ctx.host_id).inspect(found.name)
    return await guard_result(ctx, "inspect", _describe(detail))


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days} d {hours} h"
    if hours:
        return f"{hours} h {minutes} min"
    if minutes:
        return f"{minutes} min {secs} s"
    return f"{secs} s"


async def last_line_header(engine, container: str) -> str:
    """One line ending in a newline, saying how long ago the container last wrote a log line
    (`last line written 3 min 12 s ago (2026-11-12T10:14:07Z)`) or that it has written nothing,
    and "" when the engine cannot say: a fake engine, a log driver that cannot be read, a first
    word that is no timestamp. Only the parsed time is shown, never the line, so the header
    carries nothing the container wrote. Both times are the engine host's, so the age does not
    depend on the clock of the machine Bleep runs on."""
    if not isinstance(engine, LogClock):
        return ""
    try:
        last = await engine.last_log(container)
    # The header is a nicety: no failure of this extra call may cost the logs already read.
    except Exception:
        return ""
    if last is None:
        return ""
    if last.at is None:
        return "no output yet\n"
    when = last.at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"last line written {_ago((last.now - last.at).total_seconds())} ago ({when})\n"


async def _logs(
    ctx: ToolContext, container: str, tail: int = 200, since_minutes: int | None = None
) -> str:
    found = _find(ctx, container)
    if found is None:
        return NOT_FOUND
    since = _since(timedelta(minutes=since_minutes)) if since_minutes else None
    engine = ctx.registry.engine(ctx.host_id)
    text = await engine.logs(found.name, tail=tail, since=since)
    header = await last_line_header(engine, found.name)
    return header + await guard_result(ctx, "logs", text or "(no log lines)")


async def _stats(ctx: ToolContext, container: str) -> str:
    found = _find(ctx, container)
    if found is None:
        return NOT_FOUND
    s = await ctx.registry.engine(ctx.host_id).stats(found.name)
    return (
        f"{found.name}: cpu {s.cpu_percent:.1f}% (100 = one core), "
        f"memory {_size(s.mem_usage)} of {_size(s.mem_limit)} ({s.mem_percent:.1f}%), "
        f"network rx {_size(s.net_rx)} tx {_size(s.net_tx)}, "
        f"block read {_size(s.block_read)} write {_size(s.block_write)}, pids {s.pids}"
    )


async def _events(ctx: ToolContext, minutes: int = 30) -> str:
    now = datetime.now(UTC)
    events = await ctx.registry.engine(ctx.host_id).events(now - timedelta(minutes=minutes), now)
    events = [e for e in events if not _own_event(ctx, e)]
    if not events:
        return await guard_result(ctx, "events", f"(no events in the last {minutes} minutes)")
    lines = [
        f"{e.time.isoformat()} {e.type} {e.action} {e.container_name or e.container_id or ''}"
        for e in sorted(events, key=lambda e: e.time)
    ]
    return await guard_result(ctx, "events", "\n".join(lines))


SPECS = [
    ToolSpec(
        "list_containers",
        "read",
        "docker",
        lambda ctx: _tool(
            ctx,
            "list_containers",
            "List every container on this incident's host as a table: name, service, state, "
            "health, image and restart count. Use it first to see what is running, what is "
            "unhealthy and which names to pass to the other tools." + UNTRUSTED,
            NoArgs,
            _list,
        ),
    ),
    ToolSpec(
        "inspect_container",
        "read",
        "docker",
        lambda ctx: _tool(
            ctx,
            "inspect_container",
            "Get a compact summary of one container: image, state, health, its health check "
            "(command, interval, timeout, retries) and the last probes (up to 5, oldest first), "
            "restart policy and count, exit code, OOMKilled, start and finish times, ports, "
            "mounts, memory limit, labels. Use it to learn why a container stopped, keeps "
            "restarting or is unhealthy. The health check runs its command every interval; "
            "the container turns unhealthy after retries failed probes in a row. A probe that "
            "ran longer than the timeout is recorded with exit code -1 and an output starting "
            "'Health check exceeded timeout'. A probe can also time out inside its own command "
            "when its client gives up first: the exit code is then the command's own (often 1) "
            "and the output says so (for example 'timed out'). Both mean the service did not "
            "answer in time. Any other failing probe has the command's exit code and output. "
            "A probe's output is cut to about 200 characters, keeping its start and its last "
            "line. Exit code -1 with another output means Docker could not run the probe. "
            "Environment "
            "variable values that are plain settings are shown as they are (for example "
            "HOST=127.0.0.1, PORT=3000, NODE_ENV=production); a value under a secret-sounding "
            "name or one that looks like a token or key is hidden. A URL to a "
            "common network service (http, postgres, redis, amqp and the like) keeps its scheme, "
            "host and port, so you can see where a setting points (for example "
            "REDIS_URL=redis://<redacted>@cache.internal:6379/<redacted>); its user, password, "
            "path, query and fragment stay hidden. A URL is hidden whole when it cannot be read "
            "unambiguously, its scheme is not such a service or its host looks like a token, so "
            "a bare <redacted> says nothing about the value's shape." + UNTRUSTED,
            ContainerArgs,
            _inspect,
        ),
    ),
    ToolSpec(
        "container_logs",
        "read",
        "docker",
        lambda ctx: _tool(
            ctx,
            "container_logs",
            "Read the most recent log lines of one container (tail, default 200, max 1000; "
            "optionally only the last since_minutes, max 1440). Use it to find the error behind "
            "an incident. Secrets are redacted, repeated lines are collapsed and long output is "
            "cut to its end. The result starts with how long ago the container last wrote a log "
            "line, when the engine can tell." + UNTRUSTED,
            LogsArgs,
            _logs,
        ),
    ),
    ToolSpec(
        "container_stats",
        "read",
        "docker",
        lambda ctx: _tool(
            ctx,
            "container_stats",
            "Get one container's current CPU, memory, network, disk and process counts. Use it to "
            "check for memory pressure or a CPU spike. Only works on a running container.",
            ContainerArgs,
            _stats,
        ),
    ),
    ToolSpec(
        "recent_events",
        "read",
        "docker",
        lambda ctx: _tool(
            ctx,
            "recent_events",
            "List Docker events on this host from the last N minutes (default 30, max 240): "
            "starts, dies, restarts, health changes, OOM kills. Use it to build a timeline of "
            "what happened and in which order." + UNTRUSTED,
            EventsArgs,
            _events,
        ),
    ),
]
