"""Gather: collect what is known about the incident before any model runs.

Deterministic, no model call. A service gets its containers, the last 200 log lines of the
affected ones, the host's basics and the host and service rules; a host gets its basics and the
containers using the most memory, Bleep's own left out. A look on request checks the service's
URLs too, as the watcher does, since the person asked about now (#423); a service with none is
noted as having none (#445). Similar past incidents
and the service's facts come from long-term memory. Whatever cannot be read (an unreachable
host, a vanished service) becomes a line in `gaps`, never an error, so the run goes on with what
it has.
"""

import asyncio
import logging

import httpx

from bleep.graph.deps import GraphDeps
from bleep.graph.host_text import service_id_shown
from bleep.graph.look import bare_look
from bleep.graph.state import IncidentState, Node
from bleep.hosts.config import UrlCheckConfig
from bleep.hosts.discovery import is_self
from bleep.hosts.engine import ContainerNotFound, EngineUnavailable
from bleep.hosts.url_check import check_url
from bleep.schemas.graph import ContainerMemory, GatherContext, UrlReading
from bleep.schemas.incidents import Signal, Subject
from bleep.schemas.services import HostState, Service
from bleep.tools.common import guard_result
from bleep.tools.registry import ToolContext

log = logging.getLogger(__name__)

LOG_TAIL = 200  # lines per container
MAX_LOG_CONTAINERS = 5  # so a large service cannot flood the prompt
TOP_MEMORY = 5
STATS_AT_ONCE = 8  # concurrent `docker stats` calls on one host


def _why(error: Exception) -> str:
    """A short, fixed reason: engine messages are host text and do not belong in Bleep's own."""
    if isinstance(error, EngineUnavailable):
        return "host unreachable"
    if isinstance(error, ContainerNotFound):
        return "container not found"
    return type(error).__name__


def _affected(service: Service, signals: list[Signal]) -> list[str]:
    """The containers the signals name; when none do (a URL check), every one of the service."""
    names = [c.name for c in service.containers]
    named = {s.container for s in signals if s.container}
    return [n for n in names if n in named] or names


def make(deps: GraphDeps) -> Node:
    async def gather(state: IncidentState) -> dict:
        subject = state["subject"]
        signals = state.get("signals", [])
        ctx = ToolContext(state["incident_id"], subject.host_id, deps.registry, deps.guard_log)
        context = GatherContext(at=deps.clock())

        host = next((h for h in deps.registry.hosts() if h.config.id == subject.host_id), None)
        if host is None:
            context.gaps.append(f"host {subject.host_id} is not in the hosts file")
            return {"context": context}
        try:
            await deps.registry.refresh(subject.host_id)  # the state now, not the last poll's
        except Exception as error:
            context.gaps.append(f"container list: {_why(error)}")
        host = next(h for h in deps.registry.hosts() if h.config.id == subject.host_id)
        if not host.reachable:
            context.gaps.append("host unreachable: containers and basics are the last ones seen")
        context.basics = host.basics
        context.host_rules = list(host.config.rules)

        if subject.kind == "service":
            await _service(deps, ctx, subject, signals, context)
        else:
            await _host(deps, host, context)
        await _recall(deps, subject, signals, context)
        return {"context": context}

    return gather


async def _service(
    deps: GraphDeps,
    ctx: ToolContext,
    subject: Subject,
    signals: list[Signal],
    context: GatherContext,
) -> None:
    service = deps.registry.service(subject.service_id or "")
    if service is None:
        shown = service_id_shown(subject)
        context.gaps.append(f"service {shown} is no longer on host {subject.host_id}")
        return
    context.service_name = service.name
    context.containers = list(service.containers)
    context.service_rules = list(service.rules)
    names = _affected(service, signals)[:MAX_LOG_CONTAINERS]
    if bare_look(signals) and service.urls:
        _, context.urls = await asyncio.gather(
            _read_logs(deps, ctx, subject.host_id, names, context),
            _check_urls(deps, service.urls),
        )
    else:
        context.no_urls = bare_look(signals)
        await _read_logs(deps, ctx, subject.host_id, names, context)


async def _read_logs(
    deps: GraphDeps, ctx: ToolContext, host_id: str, names: list[str], context: GatherContext
) -> None:
    for name in names:
        try:
            text = await deps.registry.engine(host_id).logs(name, tail=LOG_TAIL)
        except Exception as error:
            context.gaps.append(f"logs of {name}: {_why(error)}")
            continue
        context.logs[name] = await guard_result(ctx, "logs", text or "(no log lines)")


async def _check_urls(deps: GraphDeps, urls: list[UrlCheckConfig]) -> list[UrlReading]:
    owned = deps.http is None
    client = deps.http or httpx.AsyncClient()
    try:
        checks = await asyncio.gather(*(check_url(client, cfg) for cfg in urls))
    finally:
        if owned:
            await client.aclose()
    return [UrlReading(url=c.url, ok=c.ok, status=c.status, error=c.error) for c in checks]


async def _host(deps: GraphDeps, host: HostState, context: GatherContext) -> None:
    host_id = host.config.id
    try:
        engine = deps.registry.engine(host_id)
        listed = await engine.containers()
    except Exception as error:
        context.gaps.append(f"container list: {_why(error)}")
        return
    # Bleep's own containers are never read or shown, as in the read tools (#297).
    running = [c.name for c in listed if c.state == "running" and not is_self(c.labels)]
    limit = asyncio.Semaphore(STATS_AT_ONCE)

    async def memory(name: str) -> ContainerMemory | None:
        async with limit:
            try:
                s = await engine.stats(name)
            except Exception as error:
                log.info("gather: no stats for %s on %s: %s", name, host_id, _why(error))
                return None
        return ContainerMemory(
            name=name, mem_usage=s.mem_usage, mem_limit=s.mem_limit, mem_percent=s.mem_percent
        )

    found = [m for m in await asyncio.gather(*(memory(n) for n in running)) if m is not None]
    found.sort(key=lambda m: m.mem_usage, reverse=True)
    context.top_memory = [m for m in found if m.mem_usage > 0][:TOP_MEMORY]


async def _recall(
    deps: GraphDeps, subject: Subject, signals: list[Signal], context: GatherContext
) -> None:
    """The top 3 similar past incidents (the same service's first) and the service's facts."""
    what = context.service_name or subject.host_id
    kinds = ", ".join(dict.fromkeys(s.kind for s in signals))
    try:
        context.similar = await deps.memory.similar(f"{what}: {kinds}", subject.service_id, k=3)
    except Exception as error:
        log.warning("gather: memory recall failed: %s", _why(error))
        context.gaps.append(f"similar incidents: {_why(error)}")
    if subject.service_id is None:
        return
    try:
        context.facts = await deps.memory.facts(subject.service_id)
    except Exception as error:
        log.warning("gather: reading service facts failed: %s", _why(error))
        context.gaps.append(f"service facts: {_why(error)}")
