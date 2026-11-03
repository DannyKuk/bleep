"""The settings a person changes from the dashboard, kept in SQLite and put in force at once.

Precedence is database override > hosts file > defaults (and the env for the model choice). The
store keeps only what was changed, one section per row; the values in force are computed from the
layers below on every read, so clearing an override brings the hosts file's value back. It is the
graph's `SettingsSource`: nodes read budgets, models and enabled actions from it as they run.
Secrets never get in: the API refuses them before they reach `update`, and `load` refuses a
stored row that holds one. See docs/design/settings.md.
"""

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from bleep.graph.deps import CORE_ACTIONS
from bleep.guard.secret_fields import find_secrets
from bleep.hosts.config import HostConfig, ServiceOverrides, UrlCheckConfig
from bleep.hosts.registry import Registry
from bleep.incidents.repository import utcnow
from bleep.plugins.registry import PluginError, PluginRegistry, UnknownPlugin
from bleep.schemas.plugins import PluginUpdate
from bleep.schemas.settings import (
    BudgetOverrides,
    Budgets,
    HostSettings,
    HostSettingsView,
    ModelChoice,
    ModelOverrides,
    ModelSettings,
    ServiceSettings,
    ServiceSettingsView,
    SettingsUpdate,
    ThresholdOverrides,
    Thresholds,
)
from bleep.store.db import Database

log = logging.getLogger(__name__)

HOST_PREFIX = "host:"
PLUGIN_PREFIX = "plugin:"


class SettingsRefused(ValueError):
    """The update is not applied; the text says why and never holds a submitted value."""


class _PluginRow(BaseModel):
    enabled: bool = False
    settings: dict[str, Any] = {}


def settings_field_names() -> set[str]:
    """Every field name of the settings bodies: these are never taken for a secret's name
    (`incident_tokens` is a budget)."""
    models: list[type[BaseModel]] = [
        SettingsUpdate,
        ModelOverrides,
        BudgetOverrides,
        ThresholdOverrides,
        HostSettings,
        ServiceSettings,
        UrlCheckConfig,
        PluginUpdate,
    ]
    return {name for model in models for name in model.model_fields}


class SettingsStore:
    """Non-secret overrides in the `settings` table, applied to the registry and the plugins.

    `models` is the model choice from the env (`ModelSettings()` by default); the store only
    changes the fields a person may change and keeps the env's key.
    """

    def __init__(
        self,
        db: Database,
        registry: Registry,
        plugins: PluginRegistry,
        *,
        models: ModelSettings | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._db = db
        self._registry = registry
        self._plugins = plugins
        self._env_models = models if models is not None else ModelSettings()
        self._clock = clock
        # The hosts file as read at boot: the layer under every host override.
        self._hosts_file: dict[str, HostConfig] = {h.config.id: h.config for h in registry.hosts()}
        self._overrides = SettingsUpdate(hosts={})
        self._plugin_rows: dict[str, _PluginRow] = {}
        self._lock = asyncio.Lock()

    def budgets(self) -> Budgets:
        return _layer(Budgets(), self._overrides.budgets)

    def thresholds(self) -> Thresholds:
        return _layer(Thresholds(), self._overrides.thresholds)

    def models(self) -> ModelSettings:
        return _layer(self._env_models, self._overrides.models)

    def enabled_actions(self) -> set[str]:
        """The core Docker actions and those of every plugin switched on."""
        return set(CORE_ACTIONS) | self._plugins.actions()

    def overrides(self) -> SettingsUpdate:
        return self._overrides.model_copy(deep=True)

    def plugin_toggles(self) -> dict[str, bool]:
        return {name: self._plugins.is_enabled(name) for name in self._plugins.names()}

    def model_choice(self) -> ModelChoice:
        models = self.models()
        return ModelChoice(**{name: getattr(models, name) for name in ModelChoice.model_fields})

    def host_views(self) -> list[HostSettingsView]:
        """Every host's rules and service settings in force, in hosts file order."""
        views = []
        for host_id, base in self._hosts_file.items():
            config = self._effective(base)
            services = {
                key: ServiceSettingsView(
                    urls=over.urls,
                    rules=over.rules,
                    protected_containers=over.protected_containers,
                )
                for key, over in config.services.items()
            }
            views.append(
                HostSettingsView(id=host_id, name=base.name, rules=config.rules, services=services)
            )
        return views

    async def load(self) -> None:
        """Read the stored overrides and put them in force. A row that no longer fits (a host
        gone from the hosts file or a plugin from Bleep, a field renamed, a plugin's secret unset)
        is logged and left
        out, never fatal: Bleep starts on the layers below it."""
        rows = await self._rows()
        async with self._lock:
            for key, value in rows.items():
                try:
                    self._load_row(key, value)
                except (ValueError, ValidationError, PluginError, UnknownPlugin) as problem:
                    log.warning("settings: stored %s left out: %s", key, _why(problem))
            self._apply_hosts()

    async def update(self, update: SettingsUpdate) -> None:
        """Store and apply `update` (see `SettingsUpdate` for what replaces what). Raises
        `SettingsRefused` with nothing changed for a host not in the hosts file, an invalid
        merged host, or a secret."""
        _refuse_secrets(update.model_dump(mode="json", exclude_none=True))
        async with self._lock:
            merged = self._overrides.model_copy(deep=True)
            writes: dict[str, BaseModel] = {}
            for section in ("models", "budgets", "thresholds"):
                given = getattr(update, section)
                if given is not None:
                    setattr(merged, section, given)
                    writes[section] = given
            for host_id, host in (update.hosts or {}).items():
                base = self._hosts_file.get(host_id)
                if base is None:
                    raise SettingsRefused(f"no host {host_id!r} in the hosts file")
                _merged_host(base, host)  # raises on what the hosts file itself would refuse
                hosts = merged.hosts if merged.hosts is not None else {}
                if host.model_dump(exclude_none=True, exclude_defaults=True):
                    hosts[host_id] = host
                else:
                    hosts.pop(host_id, None)
                merged.hosts = hosts
                writes[f"{HOST_PREFIX}{host_id}"] = host
            await self._write(writes)
            self._overrides = merged
            self._apply_hosts()

    async def set_plugin(self, name: str, update: PluginUpdate) -> None:
        """Configure and switch a plugin, then store it. Raises `UnknownPlugin`, or `PluginError`
        and `SettingsRefused` with the plugin as it was."""
        _refuse_secrets(update.model_dump(mode="json", exclude_none=True))
        async with self._lock:
            before = self._plugins.snapshot(name)
            old = self._plugin_rows.get(name, _PluginRow())
            on = update.enabled if update.enabled is not None else self._plugins.is_enabled(name)
            self._plugins.set(name, update.settings, on)
            settings = old.settings
            if update.settings is not None:
                settings = self._plugins.settings(name).model_dump(mode="json")
            row = _PluginRow(enabled=on, settings=settings)
            try:
                await self._write({f"{PLUGIN_PREFIX}{name}": row})
            except BaseException:
                self._plugins.restore(name, before)
                raise
            self._plugin_rows[name] = row

    def _load_row(self, key: str, value: dict[str, Any]) -> None:
        if find_secrets(value, settings_field_names()):
            raise ValueError("it holds something shaped like a secret")
        if key == "models":
            self._overrides.models = ModelOverrides.model_validate(value)
        elif key == "budgets":
            self._overrides.budgets = BudgetOverrides.model_validate(value)
        elif key == "thresholds":
            self._overrides.thresholds = ThresholdOverrides.model_validate(value)
        elif key.startswith(HOST_PREFIX):
            host_id = key.removeprefix(HOST_PREFIX)
            if host_id not in self._hosts_file:
                raise ValueError("the host is not in the hosts file")
            host = HostSettings.model_validate(value)
            _merged_host(self._hosts_file[host_id], host)
            self._host_overrides()[host_id] = host
        elif key.startswith(PLUGIN_PREFIX):
            name = key.removeprefix(PLUGIN_PREFIX)
            row = _PluginRow.model_validate(value)
            # Settings first, so a plugin that cannot be switched on (its secret is unset now)
            # still keeps them; the row stays on, and the plugin is on again once the secret is.
            self._plugins.configure(name, row.settings)
            self._plugin_rows[name] = row
            if row.enabled:
                self._plugins.enable(name, True)
        else:
            raise ValueError("unknown section")

    def _host_overrides(self) -> dict[str, HostSettings]:
        if self._overrides.hosts is None:
            self._overrides.hosts = {}
        return self._overrides.hosts

    def _apply_hosts(self) -> None:
        for base in self._hosts_file.values():
            config = self._effective(base)
            self._registry.override(base.id, config.rules, config.services)

    def _effective(self, base: HostConfig) -> HostConfig:
        host = self._host_overrides().get(base.id)
        return _merged_host(base, host) if host is not None else base

    async def _rows(self) -> dict[str, dict[str, Any]]:
        conn = await self._db.connect()
        try:
            async with conn.execute("SELECT key, value FROM settings ORDER BY key") as cursor:
                found = await cursor.fetchall()
        finally:
            await conn.close()
        rows = {}
        for row in found:
            try:
                rows[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                log.warning("settings: stored %s left out: not JSON", row["key"])
        return rows

    async def _write(self, sections: dict[str, BaseModel]) -> None:
        """Every section in one transaction; an empty one is deleted, so its layer below shows."""
        if not sections:
            return
        now = self._clock().isoformat()
        conn = await self._db.connect()
        try:
            for key, model in sections.items():
                value = model.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
                if value:
                    await conn.execute(
                        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
                        "ON CONFLICT (key) DO UPDATE SET value = excluded.value, "
                        "updated_at = excluded.updated_at",
                        (key, json.dumps(value, sort_keys=True), now),
                    )
                else:
                    await conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            await conn.commit()
        finally:
            await conn.close()


def _layer[T: BaseModel](base: T, overrides: BaseModel | None) -> T:
    if overrides is None:
        return base
    return base.model_copy(update=overrides.model_dump(exclude_none=True))


def _merged_host(base: HostConfig, host: HostSettings) -> HostConfig:
    """The hosts file's host with `host`'s overrides on top, validated like the hosts file."""
    services: dict[str, ServiceOverrides] = dict(base.services)
    for key, over in host.services.items():
        below = services.get(key, ServiceOverrides())
        protected = list(
            dict.fromkeys([*below.protected_containers, *(over.protected_containers or [])])
        )
        services[key] = below.model_copy(
            update={
                "urls": over.urls if over.urls is not None else below.urls,
                "rules": over.rules if over.rules is not None else below.rules,
                "protected_containers": protected,
            }
        )
    data = base.model_dump() | {
        "rules": host.rules if host.rules is not None else base.rules,
        "services": {key: s.model_dump() for key, s in services.items()},
    }
    try:
        return HostConfig.model_validate(data)
    except ValidationError as error:
        raise SettingsRefused(f"host {base.id}: {_why(error)}") from None


def check_body(data: Any) -> None:
    """Raise `SettingsRefused` when a settings body (decoded JSON, not validated yet) carries
    anything secret-shaped, at any depth, in a key or a value."""
    _refuse_secrets(data)


def _refuse_secrets(data: Any) -> None:
    found = find_secrets(data, settings_field_names())
    if found:
        raise SettingsRefused(
            f"settings never hold secrets; {', '.join(found)} looks like one. "
            "Secrets go in the environment or a mounted file named in the hosts file"
        )


def _why(problem: Exception) -> str:
    """The reason without the input: a validation error's text would quote the value."""
    if isinstance(problem, ValidationError):
        return "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'value'}: {e['msg']}"
            for e in problem.errors(include_input=False, include_url=False)
        )
    return str(problem)
