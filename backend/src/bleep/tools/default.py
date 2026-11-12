"""The registry Bleep runs with."""

from bleep.hosts.github import GithubClient
from bleep.tools.docker_read import SPECS as DOCKER_READ
from bleep.tools.github import DeploySource, github_specs
from bleep.tools.host_read import SPECS as HOST_READ
from bleep.tools.knowledge import Searcher, knowledge_specs
from bleep.tools.memory import Recall, recall_specs
from bleep.tools.registry import ToolRegistry, ToolSource


def default_registry(
    memory: Recall | None = None,
    plugins: ToolSource | None = None,
    github: GithubClient | None = None,
    knowledge: Searcher | None = None,
    deploys: DeploySource | None = None,
) -> ToolRegistry:
    """The read tools; `recall_incidents` too when there is a memory to read, the tools of the
    plugins switched on at the moment the catalogue is read, the GitHub tools when there is
    a client (each service with a `repo` is then offered them; `deploys` tells them which commit
    runs), and `knowledge_search` when there is a knowledge index."""
    registry = ToolRegistry(plugins)
    for spec in [
        *DOCKER_READ,
        *HOST_READ,
        *(recall_specs(memory) if memory else []),
        *(github_specs(github, deploys=deploys) if github else []),
        *(knowledge_specs(knowledge) if knowledge is not None else []),
    ]:
        registry.register(spec)
    return registry
