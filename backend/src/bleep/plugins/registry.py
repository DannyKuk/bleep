"""The plugins Bleep has, which are switched on, and what those add to the tools and actions.

The registry keeps its state in memory; the settings store (`incidents/settings_store.py`) saves
a person's choices and replays them here on boot. Tools, act handlers and enabled actions are
read from it every time they are used, so switching a plugin off takes its tools out of the next
Investigate round, makes its actions fail the plan check, and leaves Execute without a handler for
them even for a plan approved before.
"""

import asyncio
import logging
import os
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, SecretStr, ValidationError

from bleep.guard.log import GuardLogger
from bleep.guard.plan_check import KNOWN_ACTIONS
from bleep.guard.redact import REDACTED, redact_text
from bleep.guard.secret_fields import is_secret_field
from bleep.plugins.base import Plugin, PluginContext
from bleep.schemas.plugins import PluginHealth, PluginSecret, PluginView
from bleep.tools.registry import ActHandler, ToolSpec, check_kind

log = logging.getLogger(__name__)

HEALTH_TIMEOUT_S = 5.0


class PluginError(ValueError):
    """A plugin cannot be switched on or configured that way; the text says why in Bleep's own
    words and never holds a secret's value."""


class UnknownPlugin(KeyError):
    """No plugin by that name."""


@dataclass
class _Active:
    ctx: PluginContext
    tools: list[ToolSpec]
    handlers: dict[str, ActHandler]


class PluginRegistry:
    """`plugins` are every plugin Bleep has; none is switched on until `enable` says so.

    `env` is where the secrets are read from (the process environment by default), and
    `grant_secret` the `BLEEP_SECRET` act handlers check approval grants with.
    """

    def __init__(
        self,
        plugins: Sequence[Plugin],
        grant_secret: SecretStr,
        *,
        guard_log: GuardLogger | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._plugins: dict[str, Plugin] = {}
        for plugin in plugins:
            _check_plugin(plugin)
            if plugin.name in self._plugins:
                raise ValueError(f"plugin {plugin.name!r} is registered twice")
            self._plugins[plugin.name] = plugin
        self._grant_secret = grant_secret
        self._guard_log = guard_log
        self._env = env if env is not None else os.environ
        self._settings: dict[str, BaseModel] = {}
        self._active: dict[str, _Active] = {}

    def names(self) -> list[str]:
        return list(self._plugins)

    def get(self, name: str) -> Plugin:
        if name not in self._plugins:
            raise UnknownPlugin(name)
        return self._plugins[name]

    def enabled(self) -> list[Plugin]:
        return [self._plugins[name] for name in self._active]

    def is_enabled(self, name: str) -> bool:
        return name in self._active

    def settings(self, name: str) -> BaseModel:
        """The plugin's settings now: what `configure` set, else the model's defaults."""
        plugin = self.get(name)
        if name not in self._settings:
            self._settings[name] = self._validate(plugin, {})
        return self._settings[name]

    def check_settings(self, name: str, values: Mapping[str, Any]) -> BaseModel:
        """`values` validated as the plugin's settings, without applying them."""
        return self._validate(self.get(name), values)

    def configure(self, name: str, values: Mapping[str, Any]) -> None:
        """Replace the plugin's settings; a switched-on plugin rebuilds its tools with them."""
        self.set(name, values, self.is_enabled(name))

    def enable(self, name: str, on: bool) -> None:
        """Switch the plugin on or off. On needs valid settings and every secret it names set."""
        self.set(name, None, on)

    def set(self, name: str, values: Mapping[str, Any] | None, on: bool) -> None:
        """New settings (None keeps them) and on or off at once; raises `PluginError` with
        nothing changed when the settings are invalid or the plugin cannot be switched on."""
        plugin = self.get(name)
        settings = self.check_settings(name, values) if values is not None else None
        active = self._activate(plugin, settings or self.settings(name)) if on else None
        if settings is not None:
            self._settings[name] = settings
        if active is not None:
            self._active[name] = active
        else:
            self._active.pop(name, None)

    def snapshot(self, name: str) -> tuple[BaseModel | None, Any]:
        """The plugin's state as it is, for `restore` after a change that could not be saved."""
        return self._settings.get(name), self._active.get(name)

    def restore(self, name: str, state: tuple[BaseModel | None, Any]) -> None:
        settings, active = state
        for store, value in ((self._settings, settings), (self._active, active)):
            if value is None:
                store.pop(name, None)
            else:
                store[name] = value

    def tool_specs(self) -> list[ToolSpec]:
        return [spec for active in self._active.values() for spec in active.tools]

    def actions(self) -> set[str]:
        """The actions the switched-on plugins add to the plan check's enabled set."""
        return {action for active in self._active.values() for action in active.handlers}

    def act_tools(self, core: Mapping[str, ActHandler]) -> Mapping[str, ActHandler]:
        """`core` plus the switched-on plugins' act handlers, read at each lookup."""
        return _ActTools(core, self)

    def handler(self, action: str) -> ActHandler | None:
        return next(
            (a.handlers[action] for a in self._active.values() if action in a.handlers), None
        )

    async def view(self, name: str) -> PluginView:
        """What the API shows: settings and which secrets are set, never a secret's value."""
        plugin = self.get(name)
        active = self._active.get(name)
        return PluginView(
            name=name,
            title=plugin.title,
            enabled=active is not None,
            settings=self._shown_settings(name),
            settings_schema=plugin.settings_model.model_json_schema(),
            secrets=[PluginSecret(env=env, set=bool(self._env.get(env))) for env in plugin.secrets],
            health=await self._health(plugin, active.ctx) if active is not None else None,
        )

    def _shown_settings(self, name: str) -> dict[str, Any]:
        try:
            return self.settings(name).model_dump(mode="json")
        except PluginError:  # required settings not given yet: nothing to show
            return {}

    def _activate(self, plugin: Plugin, settings: BaseModel) -> _Active:
        missing = [env for env in plugin.secrets if not self._env.get(env)]
        if missing:
            raise PluginError(
                f"plugin {plugin.name} needs {', '.join(missing)} set in the environment first"
            )
        ctx = PluginContext(
            settings=settings,
            secrets={env: SecretStr(self._env[env]) for env in plugin.secrets},
            grant_secret=self._grant_secret,
            guard_log=self._guard_log,
        )
        tools = plugin.tools(ctx)
        handlers = plugin.act_handlers(ctx)
        _check_built(plugin, tools, handlers)
        return _Active(ctx, tools, handlers)

    def _validate(self, plugin: Plugin, values: Mapping[str, Any]) -> BaseModel:
        try:
            return plugin.settings_model.model_validate(dict(values))
        except ValidationError as error:
            where = {".".join(str(p) for p in e["loc"]) or "settings" for e in error.errors()}
            fields = sorted(where)
            raise PluginError(
                f"plugin {plugin.name}: invalid settings ({', '.join(fields)})"
            ) from None

    async def _health(self, plugin: Plugin, ctx: PluginContext) -> PluginHealth:
        """The plugin's own check, bounded in time; its text is redacted in case it echoes one."""
        try:
            health = await asyncio.wait_for(plugin.health(ctx), HEALTH_TIMEOUT_S)
        except TimeoutError:
            return PluginHealth(ok=False, detail="the health check timed out")
        except Exception as error:  # a broken plugin must not break the plugins page
            log.warning("plugin %s: health check failed: %s", plugin.name, type(error).__name__)
            return PluginHealth(ok=False, detail="the health check failed")
        detail = redact_text(health.detail)
        for value in ctx.secrets.values():
            if secret := value.get_secret_value():
                detail = detail.replace(secret, REDACTED)
        return PluginHealth(ok=health.ok, detail=detail)


class _ActTools(Mapping[str, ActHandler]):
    def __init__(self, core: Mapping[str, ActHandler], plugins: PluginRegistry) -> None:
        self._core = core
        self._plugins = plugins

    def __getitem__(self, action: str) -> ActHandler:
        handler = self._core.get(action) or self._plugins.handler(action)
        if handler is None:
            raise KeyError(action)
        return handler

    def __iter__(self) -> Iterator[str]:
        return iter([*self._core, *sorted(self._plugins.actions() - set(self._core))])

    def __len__(self) -> int:
        return len(set(self._core) | self._plugins.actions())


def _check_plugin(plugin: Plugin) -> None:
    """Refuse a plugin whose settings could hold a secret: the settings are stored and shown."""
    model = plugin.settings_model
    if model.model_config.get("extra") != "forbid":
        raise ValueError(f"plugin {plugin.name!r}: settings_model must forbid extra fields")
    secretish = [name for name in model.model_fields if is_secret_field(name)]
    if secretish:
        raise ValueError(
            f"plugin {plugin.name!r}: settings field(s) {', '.join(secretish)} look like secrets;"
            " name the environment variable in `secrets` instead"
        )


def _check_built(plugin: Plugin, tools: list[ToolSpec], handlers: dict[str, ActHandler]) -> None:
    """A plugin's tools carry its name, and its actions are its own, known ones."""
    for spec in tools:
        check_kind(spec)
        if spec.plugin != plugin.name or not spec.name.startswith(f"{plugin.name}_"):
            raise ValueError(
                f"plugin {plugin.name!r}: tool {spec.name!r} must be named {plugin.name}_... "
                "and carry the plugin's name"
            )
    for action in handlers:
        if not action.startswith(f"{plugin.name}.") or action not in KNOWN_ACTIONS:
            raise ValueError(f"plugin {plugin.name!r}: {action!r} is not one of its actions")
