"""A Docker host in memory, read from a case file: containers, their logs, inspect details and
recent events, and what each one looks like once an approved plan has acted.

The loop-back eval (`evals/loopback.py`) runs Bleep's own graph on it: Gather and the read tools
read it, Execute's act tools act on it, and Verify checks it, so the result Verify hands back is
the one the case scripted, and nothing leaves the process. Times are minutes before the engine
was made, so a run reads as recent whatever day it runs.
"""

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from bleep.hosts.engine import ContainerNotFound
from bleep.schemas.docker import (
    ContainerDetail,
    ContainerStats,
    ContainerSummary,
    DockerEvent,
    EngineInfo,
)

COMPOSE_PROJECT = "com.docker.compose.project"
COMPOSE_SERVICE = "com.docker.compose.service"
MIB = 2**20
Health = Literal["healthy", "unhealthy", "starting", "none"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class After(_Strict):
    """A container once the plan has run: the fields given replace its own, and `logs` is
    appended to its log."""

    state: str | None = None
    health: Health | None = None
    restart_count: int | None = None
    exit_code: int | None = None
    oom_killed: bool | None = None
    mem_usage_mib: float | None = None
    logs: str = ""
    events: list[str] = Field(default_factory=list)  # Docker event actions, as `die`, `oom`


class ScriptedContainer(_Strict):
    name: str
    image: str = "app:1"
    state: str = "running"
    health: Health = "none"
    restart_policy: str = "unless-stopped"
    restart_count: int = 0
    exit_code: int | None = None
    started_minutes_ago: float = 600
    env: list[str] = Field(default_factory=list)  # names only matter: the tools hide values
    mem_limit_mib: int | None = None
    mem_usage_mib: float = 50
    oom_killed: bool = False
    logs: str = ""
    after: After | None = None


class ScriptedEvent(_Strict):
    minutes_ago: float
    container: str
    action: str  # Docker's word: die, start, oom, "health_status: unhealthy", ...


class ScriptedEngine:
    """An `Engine` over a case's containers. The first act (restart, stop or start) does what
    Docker would do to its target, then turns every container with an `after` into it; the
    act and the after's events are added to the event history."""

    def __init__(
        self,
        host_id: str,
        project: str,
        containers: Sequence[ScriptedContainer],
        events: Sequence[ScriptedEvent] = (),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.host_id = host_id
        self.clock = clock
        start = clock()
        self.scripts = {c.name: c for c in containers}
        self.summaries = {c.name: _summary(c, project, start) for c in containers}
        self.details = {
            c.name: {"oom_killed": c.oom_killed, "mem_usage_mib": c.mem_usage_mib}
            for c in containers
        }
        self.log_text = {c.name: c.logs for c in containers}
        self.history = [
            _event(e.action, self.summaries[e.container], start - timedelta(minutes=e.minutes_ago))
            for e in events
        ]
        self.acts: list[tuple[str, str]] = []  # (action, container name), in order
        self._after_applied = False
        self._stopped = asyncio.Event()

    def _name(self, ref: str) -> str:
        for name, summary in self.summaries.items():
            if ref in (name, summary.id):
                return name
        raise ContainerNotFound(self.host_id, ref)

    async def containers(self) -> list[ContainerSummary]:
        return [s.model_copy() for s in self.summaries.values()]

    async def inspect(self, container: str) -> ContainerDetail:
        name = self._name(container)
        summary, script = self.summaries[name], self.scripts[name]
        limit = script.mem_limit_mib * MIB if script.mem_limit_mib is not None else None
        return ContainerDetail(
            summary=summary.model_copy(),
            env=script.env,
            mem_limit=limit,
            oom_killed=bool(self.details[name]["oom_killed"]),
            restart_count=summary.restart_count,
        )

    async def logs(self, container: str, tail: int = 200, since: datetime | None = None) -> str:
        lines = self.log_text[self._name(container)].splitlines(keepends=True)
        return "".join(lines[-tail:]) if tail > 0 else ""

    async def stats(self, container: str) -> ContainerStats:
        name = self._name(container)
        script = self.scripts[name]
        if self.summaries[name].state != "running":
            return ContainerStats()
        usage = int(float(self.details[name]["mem_usage_mib"]) * MIB)
        limit = (script.mem_limit_mib or 8192) * MIB
        return ContainerStats(
            cpu_percent=2.0, mem_usage=usage, mem_limit=limit, mem_percent=100 * usage / limit
        )

    async def events(self, since: datetime, until: datetime) -> list[DockerEvent]:
        return [e for e in self.history if since <= e.time <= until]

    async def stream_events(self) -> AsyncIterator[DockerEvent]:
        await self._stopped.wait()
        return
        yield  # an async generator that ends when the events are stopped

    async def stop_events(self) -> None:
        self._stopped.set()

    async def info(self) -> EngineInfo:
        running = sum(s.state == "running" for s in self.summaries.values())
        return EngineInfo(
            name=self.host_id,
            server_version="27.3.1",
            operating_system="Debian GNU/Linux 12 (bookworm)",
            os_type="linux",
            cpus=4,
            mem_total=8 * 2**30,
            containers=len(self.summaries),
            containers_running=running,
        )

    async def restart(self, container: str) -> None:
        self._act("restart", container, "running")

    async def stop(self, container: str) -> None:
        self._act("stop", container, "exited")

    async def start(self, container: str) -> None:
        self._act("start", container, "running")

    async def reconnect(self) -> None:
        return None

    async def close(self) -> None:
        await self.stop_events()

    def _act(self, action: str, container: str, state: str) -> None:
        name = self._name(container)
        now = self.clock()
        self.acts.append((action, name))
        update: dict = {"state": state}
        if state == "running":
            # A container with a health check comes back healthy unless its `after` says not.
            health = "none" if self.summaries[name].health == "none" else "healthy"
            update |= {"started_at": now, "exit_code": None, "health": health}
        else:
            update |= {"finished_at": now, "exit_code": 0}
        self.summaries[name] = self.summaries[name].model_copy(update=update)
        self.history.append(_event(action, self.summaries[name], now))
        if not self._after_applied:
            self._after_applied = True
            for script in self.scripts.values():
                if script.after is not None:
                    self._apply(script.name, script.after, now)

    def _apply(self, name: str, after: After, now: datetime) -> None:
        fields = after.model_dump(
            exclude_none=True, include={"state", "health", "restart_count", "exit_code"}
        )
        self.summaries[name] = self.summaries[name].model_copy(update=fields)
        if after.oom_killed is not None:
            self.details[name]["oom_killed"] = after.oom_killed
        if after.mem_usage_mib is not None:
            self.details[name]["mem_usage_mib"] = after.mem_usage_mib
        if after.logs:
            text = self.log_text[name]
            self.log_text[name] = text + ("" if not text or text.endswith("\n") else "\n")
            self.log_text[name] += after.logs
        self.history += [_event(action, self.summaries[name], now) for action in after.events]


def _summary(c: ScriptedContainer, project: str, now: datetime) -> ContainerSummary:
    started = now - timedelta(minutes=c.started_minutes_ago)
    return ContainerSummary(
        id=f"{c.name}-0123456789ab",
        name=c.name,
        image=c.image,
        state=c.state,
        health=c.health,
        labels={COMPOSE_PROJECT: project, COMPOSE_SERVICE: c.name.removeprefix(f"{project}-")},
        restart_policy=c.restart_policy,
        restart_count=c.restart_count,
        exit_code=c.exit_code,
        started_at=started,
        finished_at=None if c.state == "running" else now,
        created_at=started - timedelta(days=2),
    )


def _event(action: str, summary: ContainerSummary, at: datetime) -> DockerEvent:
    return DockerEvent(
        time=at,
        type="container",
        action=action,
        container_id=summary.id,
        container_name=summary.name,
    )
