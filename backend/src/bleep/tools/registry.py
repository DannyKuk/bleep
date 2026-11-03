"""The catalogue of agent tools, each labelled read or act."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from langchain_core.tools import BaseTool

from bleep.guard.log import GuardLogger
from bleep.hosts.registry import Registry
from bleep.schemas.graph import ApprovalGrant, ApprovalRecord, PlanStep, StepResult

ToolKind = Literal["read", "act"]


@dataclass(frozen=True)
class ToolContext:
    """What a tool is scoped to: one incident on one host."""

    incident_id: str
    host_id: str
    registry: Registry
    guard_log: GuardLogger | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: ToolKind
    plugin: str | None
    build: Callable[[ToolContext], BaseTool]
    # Offered only where this is true for the incident's host; None means everywhere.
    offered: Callable[[ToolContext], bool] | None = None


class ToolSource(Protocol):
    """More tools, read every time the catalogue is: the switched-on plugins' tools."""

    def tool_specs(self) -> list[ToolSpec]: ...


class ActHandler(Protocol):
    """Runs one action for a step of an approved plan; Execute alone calls it, never a model.

    `check` raises `GrantInvalid` or `StepRefused` without acting when the grant or the target is
    not right (Execute runs it for every step before the first one runs) and returns the id the
    action will act on; `run` checks again and acts. `tools/docker_act.ActTool` is the Docker one.
    """

    def check(
        self,
        step: PlanStep,
        grant: ApprovalGrant | None,
        registry: Registry,
        *,
        plan: ApprovalRecord,
        step_index: int,
    ) -> str: ...

    async def run(
        self,
        step: PlanStep,
        grant: ApprovalGrant | None,
        registry: Registry,
        *,
        plan: ApprovalRecord,
        step_index: int,
    ) -> StepResult: ...


class ToolRegistry:
    """The core tools, plus those of `plugins` as they are each time the catalogue is read, so a
    plugin switched off is gone from the next Investigate round."""

    def __init__(self, plugins: ToolSource | None = None) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._plugins = plugins

    def register(self, spec: ToolSpec) -> None:
        check_kind(spec)
        if spec.name in self._specs:
            raise ValueError(f"tool {spec.name!r} is already registered")
        self._specs[spec.name] = spec

    def specs(self) -> list[ToolSpec]:
        extra = self._plugins.tool_specs() if self._plugins is not None else []
        return [*self._specs.values(), *(s for s in extra if s.name not in self._specs)]

    def read_tools(self, ctx: ToolContext) -> list[BaseTool]:
        """The read-only tools offered on `ctx`'s host. Act tools are never returned here."""
        return [
            s.build(ctx)
            for s in self.specs()
            if s.kind == "read" and (s.offered is None or s.offered(ctx))
        ]


def check_kind(spec: ToolSpec) -> None:
    if spec.kind not in ("read", "act"):
        raise ValueError(f"tool {spec.name!r} has kind {spec.kind!r}; must be 'read' or 'act'")
