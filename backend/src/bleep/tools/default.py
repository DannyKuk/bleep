"""The registry Bleep runs with."""

from bleep.hosts.github import GithubClient
from bleep.tools.docker_read import SPECS as DOCKER_READ
from bleep.tools.github import github_specs
from bleep.tools.host_read import SPECS as HOST_READ
from bleep.tools.memory import Recall, recall_specs
from bleep.tools.registry import ToolRegistry, ToolSource


def default_registry(
    memory: Recall | None = None,
    plugins: ToolSource | None = None,
    github: GithubClient | None = None,
) -> ToolRegistry:
    """The read tools; `recall_incidents` too when there is a memory to read, the tools of the
    plugins switched on at the moment the catalogue is read, and the GitHub tools when there is
    a client (each service with a `repo` is then offered them)."""
    registry = ToolRegistry(plugins)
    for spec in [
        *DOCKER_READ,
        *HOST_READ,
        *(recall_specs(memory) if memory else []),
        *(github_specs(github) if github else []),
    ]:
        registry.register(spec)
    return registry
