"""Fakes for engines, shells and chat models land here, so no test touches a real host or model."""

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict, Field

from bleep.hosts.config import HostConfig
from bleep.hosts.engine import ContainerNotFound, EngineUnavailable
from bleep.hosts.ssh import CommandResult
from bleep.plugins.base import Plugin, PluginContext
from bleep.schemas.docker import (
    ContainerDetail,
    ContainerStats,
    ContainerSummary,
    DockerEvent,
    EngineInfo,
)
from bleep.schemas.graph import PlanStep, StepResult
from bleep.schemas.plugins import PluginHealth
from bleep.tools.registry import ActHandler, ToolContext, ToolSpec


class FakeChatModel(BaseChatModel):
    """Replays a scripted list of `AIMessage`s (tool calls and usage metadata included).

    A real `BaseChatModel`, so LangGraph tool loops and `with_structured_output` work on it.
    Running past the end of the script raises, so a test never silently loops.
    """

    script: list[AIMessage]
    structured_output_unsupported: bool = False
    cursor: int = 0
    seen: list[list[BaseMessage]] = Field(default_factory=list)
    bound_tools: list[Any] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "fake-chat-model"

    def _next(self, messages: list[BaseMessage]) -> ChatResult:
        self.seen.append(list(messages))
        if self.cursor >= len(self.script):
            raise AssertionError(f"FakeChatModel script exhausted after {len(self.script)} replies")
        reply = self.script[self.cursor]
        self.cursor += 1
        return ChatResult(generations=[ChatGeneration(message=reply)])

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._next(messages)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._next(messages)

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> FakeChatModel:
        self.bound_tools = list(tools)
        return self

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        if self.structured_output_unsupported:
            raise NotImplementedError("this model has no tool support")
        return super().with_structured_output(schema, **kwargs)


def ssh_host(**overrides: Any) -> HostConfig:
    """An ssh HostConfig with test defaults; keyword arguments replace fields."""
    fields = {
        "id": "m1",
        "name": "M1",
        "kind": "ssh",
        "address": "m1.local",
        "user": "danny",
        "port": 2222,
        "key_file": Path("/keys/id_ed25519"),
        "known_hosts_file": Path("/keys/known_hosts"),
        "docker_socket": "/Users/danny/.docker/run/docker.sock",
    }
    return HostConfig(**(fields | overrides))


def socket_host(**overrides: Any) -> HostConfig:
    """A socket HostConfig with test defaults."""
    fields = {"id": "a", "name": "A", "kind": "socket", "endpoint": "unix:///var/run/docker.sock"}
    return HostConfig(**(fields | overrides))


class FakeSshListener:
    def __init__(self, listen_path: str, dest_path: str) -> None:
        self.listen_path = listen_path
        self.dest_path = dest_path
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeSshReader:
    """An SSHReader over fixed bytes; `hang` makes it wait forever after them, like `sleep 30`."""

    def __init__(self, data: bytes, hang: bool, error: BaseException | None = None) -> None:
        self._data = data
        self._hang = hang
        self._error = error

    async def read(self, n: int = -1) -> bytes:
        if self._error is not None:
            raise self._error
        if self._data:
            size = len(self._data) if n < 0 else n
            chunk, self._data = self._data[:size], self._data[size:]
            return chunk
        if self._hang:
            await asyncio.Event().wait()
        return b""


class FakeSshProcess:
    """Stands in for asyncssh.SSHClientProcess in bytes mode."""

    def __init__(
        self,
        stdout: bytes,
        stderr: bytes,
        exit_status: int | None,
        hang: bool,
        error: BaseException | None,
    ) -> None:
        self.stdout = FakeSshReader(stdout, hang, error)
        self.stderr = FakeSshReader(stderr, hang)
        self.stdin = SimpleNamespace(write_eof=lambda: None)
        self.exit_status = exit_status
        self.terminated = False
        self.closed = False

    def terminate(self) -> None:
        self.terminated = True

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class FakeSshConnection:
    """Stands in for asyncssh.SSHClientConnection: forward, create_process, close, closed flag.

    `output` sets what the next process prints and how it exits; `processes` keeps every one.
    """

    def __init__(self, session_ref: dict) -> None:
        self.session_ref = session_ref
        self.closed = False
        self.listeners: list[FakeSshListener] = []
        self.commands: list[str] = []
        self.process_kwargs: list[dict] = []
        self.processes: list[FakeSshProcess] = []
        self.output: dict[str, Any] = {"stdout": b"ok\n", "stderr": b"", "exit_status": 0}
        self.hang = False
        self.error: BaseException | None = None
        self.stopped_when_closed: bool | None = None

    async def forward_local_path(self, listen_path: str, dest_path: str) -> FakeSshListener:
        listener = FakeSshListener(listen_path, dest_path)
        self.listeners.append(listener)
        return listener

    async def create_process(self, command: str, **kwargs: Any) -> FakeSshProcess:
        self.commands.append(command)
        self.process_kwargs.append(kwargs)
        process = FakeSshProcess(hang=self.hang, error=self.error, **self.output)
        self.processes.append(process)
        return process

    def close(self) -> None:
        session = self.session_ref.get("session")
        self.stopped_when_closed = session.stopped if session else None
        self.closed = True

    async def wait_closed(self) -> None:
        return None

    def is_closed(self) -> bool:
        return self.closed

    def lose(self) -> None:
        """The network went away: asyncssh marks the connection closed on its own."""
        self.closed = True


class FakeSshConnect:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple, dict]] = []
        self.connections: list[FakeSshConnection] = []
        self.session_ref: dict = {}
        self.error: BaseException | None = None

    async def __call__(self, *args, **kwargs) -> FakeSshConnection:
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error
        conn = FakeSshConnection(self.session_ref)
        self.connections.append(conn)
        return conn


@dataclass(frozen=True)
class ActCall:
    action: str
    container: str
    at: datetime


_STOP = object()


class FakeEngine:
    """An in-memory Engine: containers, logs and events live in plain attributes.

    Act calls are recorded in `acts` (and change the container's state), `emit` feeds open event
    streams and the `events` history, `drop()` makes the stream and every call raise
    `EngineUnavailable` until `restore()`.
    """

    def __init__(
        self,
        host_id: str = "test",
        containers: Sequence[ContainerSummary] = (),
        *,
        details: dict[str, ContainerDetail] | None = None,
        logs: dict[str, str] | None = None,
        stats: dict[str, ContainerStats] | None = None,
        events: Sequence[DockerEvent] = (),
        info: EngineInfo | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.host_id = host_id
        self.container_list = list(containers)
        self.details = details or {}
        self.log_text = logs or {}
        self.stats_by_name = stats or {}
        self.history = list(events)
        self.engine_info = info or EngineInfo(name=host_id)
        self.clock = clock
        self.acts: list[ActCall] = []
        self.unavailable = False
        self.reconnects = 0
        self.closed = False
        self._streams: list[asyncio.Queue] = []

    def _check(self) -> None:
        if self.unavailable or self.closed:
            raise EngineUnavailable(self.host_id, "simulated drop")

    def _find(self, ref: str) -> int:
        for index, c in enumerate(self.container_list):
            if ref in (c.id, c.name):
                return index
        raise ContainerNotFound(self.host_id, ref)

    async def containers(self) -> list[ContainerSummary]:
        self._check()
        return [c.model_copy() for c in self.container_list]

    async def inspect(self, container: str) -> ContainerDetail:
        self._check()
        summary = self.container_list[self._find(container)]
        return self.details.get(summary.name) or ContainerDetail(
            summary=summary, restart_count=summary.restart_count
        )

    async def logs(self, container: str, tail: int = 200, since: datetime | None = None) -> str:
        self._check()
        lines = self.log_text.get(self.container_list[self._find(container)].name, "")
        return "".join(lines.splitlines(keepends=True)[-tail:]) if tail > 0 else ""

    async def stats(self, container: str) -> ContainerStats:
        self._check()
        name = self.container_list[self._find(container)].name
        return self.stats_by_name.get(name) or ContainerStats()

    async def events(self, since: datetime, until: datetime) -> list[DockerEvent]:
        self._check()
        return [e for e in self.history if since <= e.time <= until]

    async def stream_events(self) -> AsyncIterator[DockerEvent]:
        self._check()
        queue: asyncio.Queue = asyncio.Queue()
        self._streams.append(queue)
        try:
            while True:
                item = await queue.get()
                if item is _STOP:
                    return
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            self._streams.remove(queue)

    async def stop_events(self) -> None:
        for queue in self._streams:
            queue.put_nowait(_STOP)

    async def info(self) -> EngineInfo:
        self._check()
        return self.engine_info

    async def restart(self, container: str) -> None:
        self._act("restart", container, "running")

    async def stop(self, container: str) -> None:
        self._act("stop", container, "exited")

    async def start(self, container: str) -> None:
        self._act("start", container, "running")

    async def reconnect(self) -> None:
        self.reconnects += 1
        self._check()

    async def close(self) -> None:
        await self.stop_events()
        self.closed = True

    def emit(self, event: DockerEvent) -> None:
        """Record the event in the history and hand it to every open stream."""
        self.history.append(event)
        for queue in self._streams:
            queue.put_nowait(event)

    def drop(self) -> None:
        """Lose the connection: open streams raise, and so does every call until restore()."""
        self.unavailable = True
        for queue in self._streams:
            queue.put_nowait(EngineUnavailable(self.host_id, "simulated drop"))

    def restore(self) -> None:
        self.unavailable = False

    def _act(self, action: str, container: str, state: str) -> None:
        self._check()
        index = self._find(container)
        current = self.container_list[index]
        at = self.clock()
        self.acts.append(ActCall(action, current.name, at))
        update: dict[str, Any] = {"state": state}
        if state == "running":
            update |= {"started_at": at, "exit_code": None}
        else:
            update |= {"finished_at": at, "exit_code": 0}
        self.container_list[index] = current.model_copy(update=update)


SHELL_FIXTURES = Path(__file__).parent / "fixtures" / "shell"
_SHELL_FILES: dict[str, dict[tuple[str, ...], str]] = {
    "linux": {
        ("uname", "-s"): "uname.txt",
        ("df", "-P"): "df.txt",
        ("free", "-b"): "free.txt",
        ("cat", "/proc/loadavg"): "loadavg.txt",
        ("nproc",): "nproc.txt",
        ("uptime",): "uptime.txt",
        ("top", "-b", "-n", "1"): "top.txt",
    },
    "darwin": {
        ("uname", "-s"): "uname.txt",
        ("df", "-P"): "df.txt",
        ("vm_stat",): "vm_stat.txt",
        ("sysctl", "-n", "hw.memsize", "hw.ncpu", "vm.loadavg"): "sysctl.txt",
        ("uptime",): "uptime.txt",
        ("top", "-l", "1", "-n", "15"): "top.txt",
    },
}


class FakeShell:
    """A HostShell that replays recorded command output, keyed by argv.

    `os` picks the fixture folder (real captures from a Linux and a macOS host). `replies` replaces
    or adds an argv's result: a CommandResult, a string (stdout) or an exception to raise. An argv
    with no reply exits 127, like a missing command. Every call is kept in `calls`.
    """

    def __init__(
        self,
        os: str = "linux",
        replies: dict[tuple[str, ...], CommandResult | str | Exception] | None = None,
    ) -> None:
        self.os = os
        self.replies = replies or {}
        self.calls: list[list[str]] = []

    async def run(self, argv: list[str], timeout: float = 20.0) -> CommandResult:
        self.calls.append(list(argv))
        key = tuple(argv)
        if key in self.replies:
            reply = self.replies[key]
            if isinstance(reply, Exception):
                raise reply
            if isinstance(reply, str):
                return CommandResult(exit_code=0, stdout=reply, stderr="")
            return reply
        name = _SHELL_FILES[self.os].get(key)
        if name is None:
            return CommandResult(exit_code=127, stdout="", stderr=f"{argv[0]}: command not found")
        return CommandResult(
            exit_code=0, stdout=(SHELL_FIXTURES / self.os / name).read_text(), stderr=""
        )


COOLIFY_TOKEN = "cool-token-4f9a2b7c1d"  # the fake plugin's secret; no response may carry it


class FakeCoolifySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    team: str = "main"


class FakeActHandler:
    """An act handler that records what it was asked to run and runs nothing."""

    def __init__(self, action: str) -> None:
        self.action = action
        self.ran: list[PlanStep] = []

    def check(self, step: PlanStep, grant: Any, registry: Any, *, plan: Any, step_index: int):
        return step.target

    async def run(
        self, step: PlanStep, grant: Any, registry: Any, *, plan: Any, step_index: int
    ) -> StepResult:
        self.ran.append(step)
        return StepResult(
            step_index=step_index,
            action=step.action,
            target=step.target,
            ok=True,
            detail="redeployed",
            finished_at=datetime(2026, 11, 3, tzinfo=UTC),
        )


class FakeCoolifyPlugin(Plugin):
    """A plugin shaped like Coolify: one read tool, one act action and a token from the env.
    Its health check echoes the token, as a careless real one might."""

    name = "coolify"
    title = "Coolify"
    settings_model = FakeCoolifySettings
    secrets = ("COOLIFY_TOKEN",)

    def __init__(self) -> None:
        self.contexts: list[PluginContext] = []
        self.handler = FakeActHandler("coolify.redeploy")

    def tools(self, ctx: PluginContext) -> list[ToolSpec]:
        self.contexts.append(ctx)

        def build(tool_ctx: ToolContext) -> BaseTool:
            @tool
            def coolify_applications() -> str:
                """List the Coolify applications."""
                return "shop"

            return coolify_applications

        return [ToolSpec("coolify_applications", "read", "coolify", build)]

    def act_handlers(self, ctx: PluginContext) -> dict[str, ActHandler]:
        return {"coolify.redeploy": self.handler}

    async def health(self, ctx: PluginContext) -> PluginHealth:
        token = ctx.secrets["COOLIFY_TOKEN"].get_secret_value()
        return PluginHealth(ok=True, detail=f"reached {ctx.settings.url} as {token}")
