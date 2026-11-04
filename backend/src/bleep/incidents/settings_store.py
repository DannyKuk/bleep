"""The settings a person changes from the dashboard, kept in SQLite and put in force at once.

Precedence is database override > hosts file > defaults (and the env for the model choice). The
store keeps only what was changed, one section per row; the values in force are computed from the
layers below on every read, so clearing an override brings the hosts file's value back. It is the
graph's `SettingsSource`: nodes read budgets, models and enabled actions from it as they run.
Every change names the `version` of the overrides it was built from (each plugin has its own)
and is refused when they have changed since, so a stale page never reverts someone else's change.
Secrets never get in: the API refuses them before they reach `update`, and `load` refuses a stored
row that holds one. See docs/design/settings.md.
"""

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from bleep.graph.deps import CORE_ACTIONS
from bleep.guard.redact import REDACTED
from bleep.guard.secret_fields import find_secrets
from bleep.hosts.config import HostConfig, ServiceOverrides, UrlCheckConfig
from bleep.hosts.registry import Registry
from bleep.incidents.repository import utcnow
from bleep.plugins.registry import PluginError, PluginRegistry, UnknownPlugin
from bleep.schemas.plugins import PluginUpdate, PluginView
from bleep.schemas.settings import (
    MODEL_KEY_ENV,
    BudgetOverrides,
    Budgets,
    HostConnectionView,
    HostSettings,
    HostSettingsView,
    ModelChoice,
    ModelOverrides,
    ModelSettings,
    SecretStatus,
    ServiceSettings,
    ServiceSettingsView,
    SettingsUpdate,
    ThresholdOverrides,
    Thresholds,
)
from bleep.store.db import Database
from bleep.store.secrets import SecretStore

log = logging.getLogger(__name__)

HOST_PREFIX = "host:"
PLUGIN_PREFIX = "plugin:"


class SettingsRefused(ValueError):
    """The update is not applied; the text says why and never holds a submitted value."""


class SettingsStale(Exception):
    """The update was built from overrides that have changed since; nothing is applied."""

    def __init__(self, current: str) -> None:
        super().__init__("the settings changed since this version was read")
        self.current = current


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
    changes the fields a person may change. The model key is the env's, or with `secrets` the one
    in force there (the env's, else the dashboard's), read at every call.
    """

    def __init__(
        self,
        db: Database,
        registry: Registry,
        plugins: PluginRegistry,
        *,
        models: ModelSettings | None = None,
        secrets: SecretStore | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._db = db
        self._registry = registry
        self._plugins = plugins
        self._env_models = models if models is not None else ModelSettings()
        self._secrets = secrets
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
        models = _layer(self._env_models, self._overrides.models)
        if self._secrets is None:
            return models
        return models.model_copy(update={"openrouter_api_key": self._secrets.get(MODEL_KEY_ENV)})

    def enabled_actions(self) -> set[str]:
        """The core Docker actions and those of every plugin switched on."""
        return set(CORE_ACTIONS) | self._plugins.actions()

    def overrides(self) -> SettingsUpdate:
        return self._overrides.model_copy(deep=True)

    def version(self) -> str:
        """A hash of the overrides `update` replaces, as they are stored. It is computed from their
        content, so it holds across a restart and needs no counter of its own; plugins are not
        part of it."""
        sections: dict[str, BaseModel | None] = {
            name: getattr(self._overrides, name) for name in ("models", "budgets", "thresholds")
        }
        for host_id, host in (self._overrides.hosts or {}).items():
            sections[f"{HOST_PREFIX}{host_id}"] = host
        stored = {key: _stored(model) for key, model in sections.items() if model is not None}
        return _hash({key: value for key, value in stored.items() if value})

    def plugin_version(self, name: str) -> str:
        """A hash of the plugin's stored row (switched on, settings), apart from `version`, so a
        change of the other settings never makes a plugin form stale, nor the other way round."""
        row = self._plugin_rows.get(name, _PluginRow())
        return _hash(row.model_dump(mode="json"))

    async def plugin_view(self, name: str) -> PluginView:
        """The plugin as the API shows it. It waits for any settings save in progress (they share
        one lock), so the settings it shows are stored ones: `set_plugin` puts a change in force
        before it writes it, and a write that fails restores the plugin. Nothing yields between
        that wait and the reading of the version and the settings, so the version belongs to the
        settings; a change made while the health check runs makes the version older than the
        view, never newer."""
        async with self._lock:
            pass
        version = self.plugin_version(name)
        stored = self._plugin_rows.get(name, _PluginRow()).enabled
        return await self._plugins.view(
            name, version=version, switched_on=stored or self._plugins.is_enabled(name)
        )

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
                    hosts_file_protected_containers=(
                        base.services[key].protected_containers if key in base.services else []
                    ),
                )
                for key, over in config.services.items()
            }
            views.append(
                HostSettingsView(
                    id=host_id,
                    name=base.name,
                    connection=_connection(base),
                    rules=config.rules,
                    services=services,
                )
            )
        return views

    async def load(self) -> None:
        """Read the stored overrides and put them in force. A row that no longer fits (a host
        gone from the hosts file or a plugin from Bleep, a field renamed) is logged and left out,
        never fatal: Bleep starts on the layers below it. A plugin switched on that cannot run
        now (its secret is unset) keeps its row and is logged as waiting."""
        rows = await self._rows()
        async with self._lock:
            for key, value in rows.items():
                try:
                    self._load_row(key, value)
                except (ValueError, ValidationError, PluginError, UnknownPlugin) as problem:
                    log.warning("settings: stored %s left out: %s", key, _why(problem))
                except Exception as problem:  # a plugin's own code failed: Bleep starts anyway
                    log.warning("settings: stored %s left out: %s", key, type(problem).__name__)
            self._apply_hosts()

    async def update(self, update: SettingsUpdate, *, version: str) -> None:
        """Store and apply `update` (see `SettingsUpdate` for what replaces what), built from the
        overrides at `version`. Raises `SettingsStale` when they have changed since, and
        `SettingsRefused` for a host not in the hosts file, an invalid merged host, or a secret;
        either way nothing changes."""
        _refuse_secrets(update.model_dump(mode="json", exclude_none=True))
        async with self._lock:
            if version != self.version():
                raise SettingsStale(self.version())
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

    async def set_plugin(self, name: str, update: PluginUpdate, *, version: str) -> None:
        """Configure and switch a plugin, then store it; `update` was built from the plugin at
        `version`. Raises `UnknownPlugin`, or `SettingsStale`, `PluginError` and
        `SettingsRefused` with the plugin as it was."""
        _refuse_secrets(update.model_dump(mode="json", exclude_none=True))
        async with self._lock:
            self._plugins.get(name)  # an unknown plugin is UnknownPlugin, not stale
            if version != self.plugin_version(name):
                raise SettingsStale(self.plugin_version(name))
            before = self._plugins.snapshot(name)
            old = self._plugin_rows.get(name, _PluginRow())
            on = update.enabled if update.enabled is not None else self._plugins.is_enabled(name)
            self._plugins.set(name, update.settings, on)
            settings = old.settings
            if update.settings is not None:
                settings = self._plugins.settings(name).model_dump(mode="json")
            # A change of settings alone keeps the stored toggle: a plugin that is off only because
            # its secret was unset at boot stays switched on for the next boot.
            enabled = update.enabled if update.enabled is not None else old.enabled
            row = _PluginRow(enabled=enabled, settings=settings)
            try:
                await self._write({f"{PLUGIN_PREFIX}{name}": row})
            except BaseException:
                self._plugins.restore(name, before)
                raise
            self._plugin_rows[name] = row

    async def secrets_changing(self, changed: frozenset[str]) -> None:
        """Before the secret file is written: store off, then switch off, every plugin whose
        address is about to change, so that no failure after this (the file write, a crash) can
        leave a plugin stored on that would start at the new address by itself. A failed write
        here raises, and the secret file is not written."""
        async with self._lock:
            for name in self._plugins.names():
                plugin = self._plugins.get(name)
                if plugin.connection is None or plugin.connection[0] not in changed:
                    continue
                row = self._plugin_rows.get(name, _PluginRow())
                if row.enabled:
                    off = row.model_copy(update={"enabled": False})
                    await self._write({f"{PLUGIN_PREFIX}{name}": off})
                    self._plugin_rows[name] = off
                self._plugins.enable(name, False)

    async def secrets_changed(self, changed: frozenset[str]) -> None:
        """Put the secrets and addresses changed on the dashboard in force for the plugins that use
        them. A plugin whose address changed is switched off and stored off (again, in case it was
        switched on since `secrets_changing`): its token was cleared with it, and it runs again
        only when a person switches it on. Any other plugin switched
        on is built again with the values in force now, or waits for a required one now unset."""
        async with self._lock:
            for name in self._plugins.names():
                plugin = self._plugins.get(name)
                if not changed & {*plugin.secrets, *plugin.optional_secrets}:
                    continue
                row = self._plugin_rows.get(name, _PluginRow())
                if plugin.connection is not None and plugin.connection[0] in changed:
                    self._plugins.enable(name, False)
                    if row.enabled:
                        off = row.model_copy(update={"enabled": False})
                        await self._write({f"{PLUGIN_PREFIX}{name}": off})
                        self._plugin_rows[name] = off
                    log.info("settings: plugin %s switched off: its address changed", name)
                elif row.enabled or self._plugins.is_enabled(name):
                    try:
                        self._plugins.enable(name, True)
                    except PluginError as waiting:
                        self._plugins.enable(name, False)  # never keep running on the old value
                        log.warning("settings: plugin %s does not run now: %s", name, waiting)

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
                try:
                    self._plugins.enable(name, True)
                except PluginError as waiting:
                    log.warning(
                        "settings: plugin %s stays switched on but does not run: %s", name, waiting
                    )
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
                value = _stored(model)
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


def _connection(host: HostConfig) -> HostConnectionView:
    if host.kind == "socket":
        return HostConnectionView(kind="socket", endpoint=_without_userinfo(host.endpoint))
    key = host.key_file
    return HostConnectionView(
        kind="ssh",
        address=_without_userinfo(host.address),
        user=host.user,
        port=host.port,
        docker_socket=host.docker_socket,
        key_file=SecretStatus(file=str(key), set=_readable(key)) if key is not None else None,
        known_hosts_file=str(host.known_hosts_file) if host.known_hosts_file else None,
    )


def _hash(content: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[:16]


def _stored(model: BaseModel) -> dict[str, Any]:
    """A section as it is stored: only what differs from the layers below."""
    return model.model_dump(mode="json", exclude_none=True, exclude_defaults=True)


def _without_userinfo(address: str | None) -> str | None:
    """`tcp://user:pass@host:2375` as `tcp://<redacted>@host:2375`. Everything up to the last `@`
    goes, since a password may hold `@` and `/`."""
    if address is None or "@" not in address:
        return address
    scheme, sep, rest = address.partition("://")
    if not sep:
        scheme, rest = "", address
    return f"{scheme}{sep}{REDACTED}@{rest.rpartition('@')[2]}"


def _readable(path: Path) -> bool:
    """Whether Bleep could read the key file; it is never opened here."""
    return path.is_file() and os.access(path, os.R_OK)


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
        # Only where, not the message: the hosts file's own messages quote the value.
        where = sorted({".".join(str(p) for p in e["loc"]) or "host" for e in error.errors()})
        raise SettingsRefused(f"host {base.id}: invalid {', '.join(where)}") from None


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
