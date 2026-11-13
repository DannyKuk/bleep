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
from bleep.plugins.urls import Resolver
from bleep.schemas.plugins import PluginHealth
from bleep.schemas.services import CheckSuggestions, Service
from bleep.tools.github import DeploySource
from bleep.tools.registry import ActHandler, ToolSpec


@dataclass(frozen=True)
class PluginContext:
    """What a switched-on plugin builds its tools and act handlers from.

    `grant_secret` is `BLEEP_SECRET`: an act handler checks approval grants with it, exactly as
    the Docker act tools do (`tools/docker_act.py`). `public_only` is set when the plugin's
    address was set on the dashboard: the plugin must then send through
    `plugins.urls.guarded(transport, ctx.public_only)`, which reaches public addresses only.
    """

    settings: BaseModel
    secrets: Mapping[str, SecretStr] = field(repr=False)
    grant_secret: SecretStr = field(repr=False)
    guard_log: GuardLogger | None = None
    public_only: Resolver | None = None


class Plugin(ABC):
    """One plugin. Subclasses set the class attributes and build their tools from a context.

    `settings_model` must forbid unknown fields and may not have a field that looks like a secret;
    `secrets` names the environment variables the plugin needs, every one of which must be set
    before it can be switched on, and `optional_secrets` those it uses when they are set.
    `connection` names the address among them and the token sent to it: set from the dashboard,
    a new address clears the token (`store/secrets.py`). Tool
    names start with `<name>_` and act actions with `<name>.`, so a plugin can neither shadow a
    core tool nor add a Docker action.
    """

    name: ClassVar[str]
    title: ClassVar[str]
    settings_model: ClassVar[type[BaseModel]]
    secrets: ClassVar[tuple[str, ...]] = ()
    optional_secrets: ClassVar[tuple[str, ...]] = ()
    connection: ClassVar[tuple[str, str] | None] = None

    @abstractmethod
    def tools(self, ctx: PluginContext) -> list[ToolSpec]:
        """The plugin's tools, each labelled read or act; read tools join Investigate's."""

    def act_handlers(self, ctx: PluginContext) -> dict[str, ActHandler]:
        """The actions the plugin can run for an approved plan, by action name."""
        return {}

    async def check_suggestions(self, ctx: PluginContext, service: Service) -> CheckSuggestions:
        """What the plugin's own records say about `service`'s URL checks: the ones to offer,
        without those it has, and those it has that the records no longer back. Both are only
        shown on the service's page; nothing is checked or removed until a person clicks."""
        return CheckSuggestions()

    def deploys(self, ctx: PluginContext) -> DeploySource | None:
        """Which commit a service the plugin deployed runs, for `deployed_commits`; None for a
        plugin that deploys nothing."""
        return None

    @abstractmethod
    async def health(self, ctx: PluginContext) -> PluginHealth:
        """Whether the plugin's connection works now, in Bleep's own words."""
