"""The catalogue of agent tools, each labelled read or act."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from langchain_core.tools import BaseTool

from bleep.guard.log import GuardLogger
from bleep.hosts.registry import Registry

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


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.kind not in ("read", "act"):
            raise ValueError(f"tool {spec.name!r} has kind {spec.kind!r}; must be 'read' or 'act'")
        if spec.name in self._specs:
            raise ValueError(f"tool {spec.name!r} is already registered")
        self._specs[spec.name] = spec

    def specs(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def read_tools(self, ctx: ToolContext) -> list[BaseTool]:
        """The read-only tools, built for `ctx`. Act tools are never returned here."""
        return [s.build(ctx) for s in self._specs.values() if s.kind == "read"]
