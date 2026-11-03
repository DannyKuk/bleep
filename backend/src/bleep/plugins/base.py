"""What a plugin is: tools that share one connection, switched on and off as a whole.

A plugin declares its non-secret settings as a pydantic model and its secrets by environment
variable name only. The values of those variables are read when the plugin is switched on and are
handed to its tools and act handlers, never to the database, the API or a model. See
docs/design/plugins.md.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import ClassVar

from pydantic import BaseModel, SecretStr

from bleep.guard.log import GuardLogger
from bleep.schemas.plugins import PluginHealth
from bleep.tools.registry import ActHandler, ToolSpec


@dataclass(frozen=True)
class PluginContext:
    """What a switched-on plugin builds its tools and act handlers from.

    `grant_secret` is `BLEEP_SECRET`: an act handler checks approval grants with it, exactly as
    the Docker act tools do (`tools/docker_act.py`).
    """

    settings: BaseModel
    secrets: Mapping[str, SecretStr] = field(repr=False)
    grant_secret: SecretStr = field(repr=False)
    guard_log: GuardLogger | None = None


class Plugin(ABC):
    """One plugin. Subclasses set the class attributes and build their tools from a context.

    `settings_model` must forbid unknown fields and may not have a field that looks like a secret;
    `secrets` names the environment variables the plugin needs, every one of which must be set
    before it can be switched on. Tool names start with `<name>_` and act actions with `<name>.`,
    so a plugin can neither shadow a core tool nor add a Docker action.
    """

    name: ClassVar[str]
    title: ClassVar[str]
    settings_model: ClassVar[type[BaseModel]]
    secrets: ClassVar[tuple[str, ...]] = ()

    @abstractmethod
    def tools(self, ctx: PluginContext) -> list[ToolSpec]:
        """The plugin's tools, each labelled read or act; read tools join Investigate's."""

    def act_handlers(self, ctx: PluginContext) -> dict[str, ActHandler]:
        """The actions the plugin can run for an approved plan, by action name."""
        return {}

    @abstractmethod
    async def health(self, ctx: PluginContext) -> PluginHealth:
        """Whether the plugin's connection works now, in Bleep's own words."""
