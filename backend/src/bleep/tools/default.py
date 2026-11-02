"""The registry Bleep runs with."""

from bleep.tools.docker_read import SPECS as DOCKER_READ
from bleep.tools.host_read import SPECS as HOST_READ
from bleep.tools.memory import Recall, recall_specs
from bleep.tools.registry import ToolRegistry


def default_registry(memory: Recall | None = None) -> ToolRegistry:
    """The read tools; `recall_incidents` too when there is a memory to read."""
    registry = ToolRegistry()
    for spec in [*DOCKER_READ, *HOST_READ, *(recall_specs(memory) if memory else [])]:
        registry.register(spec)
    return registry
