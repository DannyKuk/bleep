"""The registry Bleep runs with."""

from bleep.tools.docker_read import SPECS as DOCKER_READ
from bleep.tools.host_read import SPECS as HOST_READ
from bleep.tools.memory import Recall, recall_specs
from bleep.tools.registry import ToolRegistry, ToolSource


def default_registry(
    memory: Recall | None = None, plugins: ToolSource | None = None
) -> ToolRegistry:
    """The read tools; `recall_incidents` too when there is a memory to read, and the tools of
    the plugins switched on at the moment the catalogue is read."""
    registry = ToolRegistry(plugins)
    for spec in [*DOCKER_READ, *HOST_READ, *(recall_specs(memory) if memory else [])]:
        registry.register(spec)
    return registry
