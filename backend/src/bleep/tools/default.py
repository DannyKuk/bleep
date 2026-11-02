"""The registry Bleep runs with."""

from bleep.tools.docker_read import SPECS as DOCKER_READ
from bleep.tools.registry import ToolRegistry


def default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for spec in DOCKER_READ:
        registry.register(spec)
    return registry
