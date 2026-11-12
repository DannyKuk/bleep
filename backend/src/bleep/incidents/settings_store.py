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
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError

from bleep.graph.deps import CORE_ACTIONS
from bleep.guard.redact import REDACTED
from bleep.guard.secret_fields import find_secrets
from bleep.hosts.config import HostConfig, ServiceOverrides, UrlCheckConfig, is_exit_name
from bleep.hosts.dashboard import DashboardHosts, HostChange
from bleep.hosts.registry import Registry
from bleep.incidents.repository import utcnow
from bleep.plugins.registry import PluginError, PluginRegistry, UnknownPlugin
from bleep.schemas.plugins import PluginUpdate, PluginView
from bleep.schemas.refusal import Reasons, Templated
from bleep.schemas.services import Service
from bleep.schemas.settings import (
    ANTHROPIC_KEY_ENV,
    DEFAULT_LANGUAGE,
    EMBEDDINGS_URL_ENV,
    ENV_NAMES,
    LOCAL_URL_ENV,
    MODEL_KEY_ENV,
    TRACER_OF_ENDPOINT,
    TRACING_ENDPOINTS,
    BudgetOverrides,
    Budgets,
    EmbeddingsChoice,
    EmbeddingsOverrides,
    GeneralOverrides,
    HostConnectionView,
    HostSettings,
    HostSettingsView,
    Language,
    ModelChoice,
    ModelOverrides,
    ModelSettings,
    SecretStatus,
    ServiceSettings,
    ServiceSettingsView,
    SettingsUpdate,
    ThresholdOverrides,
    Thresholds,
    TracingChoice,
    TracingOverrides,
)
from bleep.store.db import Database
from bleep.store.secrets import SecretStore, UnknownSecret

log = logging.getLogger(__name__)

HOST_PREFIX = "host:"
# One stored row each.
SECTIONS = ("models", "budgets", "thresholds", "general", "embeddings", "tracing")
PLUGIN_PREFIX = "plugin:"


class SettingsRefused(Templated, ValueError):
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
        GeneralOverrides,
        EmbeddingsOverrides,
        TracingOverrides,
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
    in force there (the env's, else the dashboard's), read at every call. `timezone` is
    BLEEP_TIMEZONE, the zone until one is chosen on the dashboard. `embeddings` and `tracing` are
    the fields of those sections the env sets; like the model choice's, they win (#372).
    """

    def __init__(
        self,
        db: Database,
        registry: Registry,
        plugins: PluginRegistry,
        *,
        models: ModelSettings | None = None,
        secrets: SecretStore | None = None,
        dashboard: DashboardHosts | None = None,
        clock: Callable[[], datetime] = utcnow,
        timezone: str | None = None,
        embeddings: EmbeddingsOverrides | None = None,
        tracing: TracingOverrides | None = None,
    ) -> None:
        self._db = db
        self._dashboard = dashboard
        self._registry = registry
        self._plugins = plugins
        self._env_models = models if models is not None else ModelSettings()
        self._secrets = secrets
        self._clock = clock
        self._env_timezone = timezone
        self._env_embeddings = embeddings or EmbeddingsOverrides()
        self._env_tracing = tracing or TracingOverrides()
        self._started_embeddings: EmbeddingsChoice | None = None
        self._restart_checks: dict[str, Callable[[], bool]] = {}
        self._listeners: list[Callable[[frozenset[str]], Awaitable[None]]] = []
        # The layer under every host override: the hosts file as read at boot, and the hosts
        # added on the dashboard, which come and go (`host_changed`).
        self._base: dict[str, HostConfig] = {h.config.id: h.config for h in registry.hosts()}
        self._overrides = SettingsUpdate(hosts={})
        self._plugin_rows: dict[str, _PluginRow] = {}
        self._lock = asyncio.Lock()

    def budgets(self) -> Budgets:
        return _layer(Budgets(), self._overrides.budgets)

    def thresholds(self) -> Thresholds:
        return _layer(Thresholds(), self._overrides.thresholds)

    def timezone(self) -> str | None:
        """The IANA name times are shown in: the dashboard's choice, else the env's; None for
        neither."""
        general = self._overrides.general
        chosen = general.timezone if general is not None else None
        return chosen or self._env_timezone

    def language(self) -> Language:
        """The language Bleep writes for the person in: the dashboard's choice, else English. Read
        at each use, so a change applies to the next text written."""
        general = self._overrides.general
        chosen = general.language if general is not None else None
        return chosen or DEFAULT_LANGUAGE

    def zone(self) -> tzinfo | None:
        """`timezone()` as a zone, read by Telegram at each message so a change applies at once.
        UTC needs no zone data."""
        name = self.timezone()
        if name is None:
            return None
        return UTC if name == "UTC" else ZoneInfo(name)

    def models(self) -> ModelSettings:
        """The model choice in force: the env's fields, else the dashboard's, else the defaults;
        the key and the local model's base URL as the secret store has them, read at each call."""
        models = _layer(self._env_models, self._chosen("models"))
        if self._secrets is None:
            return models
        update: dict[str, Any] = {
            "openrouter_api_key": self._secrets.get(MODEL_KEY_ENV),
            "anthropic_api_key": self._secrets.get(ANTHROPIC_KEY_ENV),
        }
        if (local_url := self._secrets.get(LOCAL_URL_ENV)) is not None:
            update["local_base_url"] = local_url.get_secret_value()
        return models.model_copy(update=update)

    def embeddings(self) -> EmbeddingsChoice:
        """The embedder chosen: the env's fields, else the dashboard's. It is built once, at the
        first use after `load` (`start_embeddings`), so a change applies at the next start."""
        return _layer(_layer(EmbeddingsChoice(), self._chosen("embeddings")), self._env_embeddings)

    def embedder_choice(self) -> EmbeddingsChoice:
        """The embedder `embeddings()` builds: an endpoint or a local server only with its model
        and address set, else the built-in model, whose model is the image's."""
        chosen = self.embeddings()
        address = self._secrets.get(EMBEDDINGS_URL_ENV) if self._secrets is not None else None
        if chosen.provider != "local" and chosen.model and address is not None:
            return chosen
        return EmbeddingsChoice(min_score=chosen.min_score)

    def start_embeddings(self) -> EmbeddingsChoice:
        """The embedder to build now, recorded as the running one, so the view can say a change
        saved since needs a restart."""
        self._started_embeddings = self.embedder_choice()
        return self._started_embeddings

    def clear_refused(self, name: str) -> SettingsRefused | None:
        """Why a secret cannot be cleared now: the address of the endpoint or the local server
        while the running embedder is that one, which would fail every embed until a restart."""
        started = self._started_embeddings
        if name == EMBEDDINGS_URL_ENV and started is not None and started.provider != "local":
            return SettingsRefused(
                f"{EMBEDDINGS_URL_ENV} cannot be cleared while an endpoint or a local "
                "server embeds the memory; choose the built-in model, restart Bleep, then clear it",
                "settings_embeddings_clear",
                name=EMBEDDINGS_URL_ENV,
            )
        return None

    def restart_when(self, section: str, needed: Callable[[], bool]) -> None:
        """`section` is listed in `restart_needed` while `needed()` holds (a running Langfuse
        tracer that a change waits for)."""
        self._restart_checks[section] = needed

    def tracing(self) -> TracingChoice:
        """The tracer in force without its keys: the env's fields, else the dashboard's. A tracer
        whose key the env sets reads its address from the env too, so a key from .env is never
        sent to an address chosen on the dashboard."""
        return _layer(_layer(TracingChoice(), self._chosen("tracing")), self._env_tracing)

    def from_env(self) -> list[str]:
        """Every field the env decides, as `section.field`."""
        return sorted(f"{section}.{field}" for section in ENV_NAMES for field in self._env(section))

    def restart_needed(self) -> list[str]:
        """The sections saved here whose change waits for the next start."""
        started = self._started_embeddings
        embeddings = started is not None and started != self.embedder_choice()
        waiting = [section for section, needed in self._restart_checks.items() if needed()]
        return (["embeddings"] if embeddings else []) + waiting

    def subscribe(self, listener: Callable[[frozenset[str]], Awaitable[None]]) -> None:
        """`listener` is awaited with the sections an update changed, and after `load` with the
        sections stored (the tracer is built again from them)."""
        self._listeners.append(listener)

    def _env(self, section: str) -> set[str]:
        """The fields of `section` the env decides."""
        if section == "models":
            return self._env_models.model_fields_set & set(ENV_NAMES["models"])
        if section == "embeddings":
            return set(self._env_embeddings.model_dump(exclude_none=True))
        fields = set(self._env_tracing.model_dump(exclude_none=True))
        for endpoint, keys in TRACING_ENDPOINTS.items():
            if any(self._key_from_env(key) for key in keys):
                fields.add(endpoint)
        return fields

    def _key_from_env(self, name: str) -> bool:
        if self._secrets is None:
            return False
        try:
            return self._secrets.state(name).env_owned
        except UnknownSecret:  # a store built without the tracers' keys (a test's)
            return False

    def _chosen(self, section: str) -> BaseModel | None:
        """The dashboard's overrides of `section` without the fields the env decides."""
        stored = getattr(self._overrides, section)
        if stored is None:
            return None
        return stored.model_copy(update=dict.fromkeys(self._env(section)))

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
            name: getattr(self._overrides, name) for name in SECTIONS
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
        """Every host's rules and service settings in force: the hosts file's in its order, then
        the dashboard's in the order they were added."""
        views = []
        for host_id, base in self._base.items():
            config = self._effective(base)
            services = {
                key: ServiceSettingsView(
                    urls=over.urls,
                    rules=over.rules,
                    protected_containers=over.protected_containers,
                    hosts_file_protected_containers=self._hosts_file_protected(base, key),
                    exits=over.exits,
                )
                for key, over in config.services.items()
            }
            views.append(
                HostSettingsView(
                    id=host_id,
                    name=base.name,
                    connection=self._connection(base),
                    rules=config.rules,
                    may_sleep=config.may_sleep,
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
            self._warn_shadowed()
        await self._tell(frozenset(s for s in SECTIONS if getattr(self._overrides, s) is not None))

    async def update(self, update: SettingsUpdate, *, version: str) -> None:
        """Store and apply `update` (see `SettingsUpdate` for what replaces what), built from the
        overrides at `version`. Raises `SettingsStale` when they have changed since, and
        `SettingsRefused` for a host not in the hosts file, an invalid merged host, or a secret;
        either way nothing changes."""
        _refuse_secrets(update.model_dump(mode="json", exclude_none=True))
        async with self._lock:
            if version != self.version():
                raise SettingsStale(self.version())
            self._refuse_env_changes(update)
            before = (self.embeddings(), self.tracing())
            merged = self._overrides.model_copy(deep=True)
            writes: dict[str, BaseModel] = {}
            for section in SECTIONS:
                given = getattr(update, section)
                if given is not None:
                    setattr(merged, section, given)
                    writes[section] = given
            for host_id, host in (update.hosts or {}).items():
                base = self._base.get(host_id)
                if base is None:
                    raise SettingsRefused(
                        f"no host {host_id!r} in the hosts file or the dashboard",
                        "settings_no_host",
                        host=repr(host_id),
                    )
                _merged_host(base, host)  # raises on what the hosts file itself would refuse
                hosts = merged.hosts if merged.hosts is not None else {}
                if host.model_dump(exclude_none=True, exclude_defaults=True):
                    hosts[host_id] = host
                else:
                    hosts.pop(host_id, None)
                merged.hosts = hosts
                writes[f"{HOST_PREFIX}{host_id}"] = host
            self._check_in_force(merged, *before)
            await self._write(writes)
            self._overrides = merged
            self._apply_hosts()
        await self._tell(frozenset(s for s in SECTIONS if s in writes))

    def _refuse_env_changes(self, update: SettingsUpdate) -> None:
        """`SettingsRefused` for a field the env decides that `update` changes. One stored before
        the env set it may be sent again as it is, or left out to clear it."""
        for section, names in ENV_NAMES.items():
            given = getattr(update, section)
            if given is None:
                continue
            stored = getattr(self._overrides, section)
            for field in sorted(self._env(section)):
                value = getattr(given, field)
                if value is None or value == (getattr(stored, field) if stored else None):
                    continue
                keys = TRACING_ENDPOINTS.get(field, ()) if section == "tracing" else ()
                from_env = [k for k in keys if self._key_from_env(k)]
                if from_env:
                    raise SettingsRefused(
                        f"{from_env[0]} is set in the environment (.env or Compose), so "
                        f"{names[field]} is read from there too; change it there",
                        "settings_key_from_env",
                        key=from_env[0],
                        name=names[field],
                    )
                raise SettingsRefused(
                    f"{names[field]} is set in the environment (.env or Compose), which wins; "
                    "change it there",
                    "settings_from_env",
                    name=names[field],
                )

    def _warn_shadowed(self) -> None:
        """Log each stored override the env now decides: it stays stored but is not in force."""
        for section, names in ENV_NAMES.items():
            stored = getattr(self._overrides, section)
            if stored is None:
                continue
            given = stored.model_dump(exclude_none=True)
            for field in sorted(self._env(section) & set(given)):
                log.warning(
                    "settings: the dashboard's %s is not in force: it is set in the environment",
                    names[field],
                )

    def _check_in_force(
        self, merged: SettingsUpdate, embeddings_before: EmbeddingsChoice, before: TracingChoice
    ) -> None:
        """`SettingsRefused` for an embedder or a tracer the merged overrides would put in force
        without what it needs, and for a tracer's address changed while a key entered for the old
        one is set."""
        current = self._overrides
        self._overrides = merged
        try:
            embeddings, tracing = self.embeddings(), self.tracing()
        finally:
            self._overrides = current
        if embeddings != embeddings_before and embeddings.provider != "local":
            if not embeddings.model:
                raise SettingsRefused(
                    "an embeddings endpoint needs its model (EMBEDDINGS_MODEL)",
                    "settings_embeddings_model",
                )
            if self._secrets is not None and self._secrets.get(EMBEDDINGS_URL_ENV) is None:
                raise SettingsRefused(
                    f"set {EMBEDDINGS_URL_ENV} first: the endpoint the embeddings are sent to",
                    "settings_embeddings_url",
                    name=EMBEDDINGS_URL_ENV,
                )
        if self._secrets is None:
            return
        for endpoint, keys in TRACING_ENDPOINTS.items():
            if getattr(tracing, endpoint) == getattr(before, endpoint):
                continue
            if set_keys := [k for k in keys if self._secrets.get(k) is not None]:
                raise SettingsRefused(
                    f"clear {' and '.join(set_keys)} first: a key is sent only to the address it "
                    f"was entered for; then set the new {ENV_NAMES['tracing'][endpoint]} and "
                    "enter the key again",
                    "settings_tracing_keys_first",
                    keys=" and ".join(set_keys),
                    name=ENV_NAMES["tracing"][endpoint],
                )
        if tracing.tracing not in ("off", before.tracing):
            endpoint = next(e for e, t in TRACER_OF_ENDPOINT.items() if t == tracing.tracing)
            missing = [k for k in TRACING_ENDPOINTS[endpoint] if self._secrets.get(k) is None]
            if missing:
                raise SettingsRefused(
                    f"TRACING={tracing.tracing} needs {' and '.join(missing)}; set it first",
                    "settings_tracing_needs",
                    tracing=tracing.tracing,
                    keys=" and ".join(missing),
                )

    async def _tell(self, changed: frozenset[str]) -> None:
        for listener in self._listeners if changed else []:
            try:
                await listener(changed)
            except Exception as error:  # the type only: a message could quote a value
                log.warning("settings: applying a change failed (%s)", type(error).__name__)

    async def expect_exits(self, service: Service, entries: list[str]) -> None:
        """Add `entries` to the service's `exits` as a dashboard override ("This is expected").
        Only adds, so it needs no version: a page saving from an older one gets its 409."""
        if any(not is_exit_name(entry) for entry in entries):  # never `*`: one tap names jobs
            raise SettingsRefused(
                "an expected job must be a container or Compose service name",
                "settings_expected_job",
            )
        async with self._lock:
            if service.host_id not in self._base:
                raise SettingsRefused(
                    f"no host {service.host_id!r} in the hosts file or dashboard",
                    "settings_no_host_here",
                    host=repr(service.host_id),
                )
            host = self._host_overrides().get(service.host_id, HostSettings()).model_copy(deep=True)
            config = self._effective(self._base[service.host_id])
            key = _settings_key(config, service)
            current = config.services[key].exits if key in config.services else []
            own = host.services.get(key, ServiceSettings()).model_copy()
            own.exits = list(dict.fromkeys([*current, *entries]))
            host.services[key] = own
            await self._replace_host(service.host_id, host)

    async def expect_sleep(self, host_id: str) -> None:
        """Mark a host as one that may sleep, as a dashboard override ("This is expected")."""
        async with self._lock:
            host = self._host_overrides().get(host_id, HostSettings()).model_copy(deep=True)
            host.may_sleep = True
            await self._replace_host(host_id, host)

    async def _replace_host(self, host_id: str, host: HostSettings) -> None:
        """Store and apply one host's overrides, validated as a page's save is (a field set on a
        model is not); the caller holds the lock."""
        base = self._base.get(host_id)
        if base is None:
            raise SettingsRefused(
                f"no host {host_id!r} in the hosts file or the dashboard",
                "settings_no_host",
                host=repr(host_id),
            )
        try:
            host = HostSettings.model_validate(host.model_dump(exclude_none=True))
        except ValidationError as error:
            raise SettingsRefused(
                f"host {host_id}: {_why(error)}",
                "settings_host_reasons",
                host=host_id,
                reasons=Reasons.of(error),
            ) from None
        _merged_host(base, host)
        await self._write({f"{HOST_PREFIX}{host_id}": host})
        self._host_overrides()[host_id] = host
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

    async def host_changed(self, change: HostChange) -> None:
        """A host added, changed or removed on the dashboard becomes, or stops being, a layer under
        the overrides. A removed host's overrides are deleted with it, so a host added later under
        the same id starts with none."""
        async with self._lock:
            if change.config is None:
                self._base.pop(change.host_id, None)
                if self._host_overrides().pop(change.host_id, None) is not None:
                    await self._write({f"{HOST_PREFIX}{change.host_id}": HostSettings()})
                return
            self._base[change.host_id] = change.config
            config = self._effective(change.config)
            self._registry.override(change.host_id, config.rules, config.services, config.may_sleep)

    def _hosts_file_protected(self, base: HostConfig, key: str) -> list[str]:
        """The hosts file's protected containers for the service held under `key`, by its key
        and by its name: discovery applies both (#230). A service not seen yet has only `key`."""
        same = {key}
        for service in self._registry.services(base.id):
            own = service.id.removeprefix(f"{base.id}/")
            if key in (own, service.name):
                same |= {own, service.name}
        found = [c for k, o in base.services.items() if k in same for c in o.protected_containers]
        return list(dict.fromkeys(found))

    def _connection(self, host: HostConfig) -> HostConnectionView:
        if host.source == "dashboard" and self._dashboard is not None:
            return self._dashboard.connection(host.id)
        return _connection(host)

    def _load_row(self, key: str, value: dict[str, Any]) -> None:
        if find_secrets(value, settings_field_names()):
            raise ValueError("it holds something shaped like a secret")
        if key == "models":
            self._overrides.models = ModelOverrides.model_validate(value)
        elif key == "budgets":
            self._overrides.budgets = BudgetOverrides.model_validate(value)
        elif key == "thresholds":
            self._overrides.thresholds = ThresholdOverrides.model_validate(value)
        elif key == "general":
            self._overrides.general = GeneralOverrides.model_validate(value)
        elif key == "embeddings":
            self._overrides.embeddings = EmbeddingsOverrides.model_validate(value)
        elif key == "tracing":
            self._overrides.tracing = TracingOverrides.model_validate(value)
        elif key.startswith(HOST_PREFIX):
            host_id = key.removeprefix(HOST_PREFIX)
            if host_id not in self._base:
                raise ValueError("the host is not in the hosts file or added on the dashboard")
            host = _salvaged_host(host_id, value)
            _merged_host(self._base[host_id], host)
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
        for base in self._base.values():
            config = self._effective(base)
            self._registry.override(base.id, config.rules, config.services, config.may_sleep)

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


def _salvaged_host(host_id: str, value: dict[str, Any]) -> HostSettings:
    """A stored host row, with every field that no longer validates left out on its own and
    logged: one bad field (an `exits` entry from an older version) must not drop the others, the
    protected containers above all."""
    try:
        return HostSettings.model_validate(value)
    except ValidationError:
        pass
    kept: dict[str, Any] = {}
    for field in ("rules", "may_sleep"):
        if field in value:
            kept |= _valid(HostSettings, field, value[field], f"host {host_id}")
    services: dict[str, Any] = {}
    raw = value.get("services")
    for key, service in raw.items() if isinstance(raw, dict) else []:
        own: dict[str, Any] = {}
        for field, given in service.items() if isinstance(service, dict) else []:
            own |= _valid(ServiceSettings, field, given, f"host {host_id}, a service")
        if own and _valid(HostSettings, "services", {key: {}}, f"host {host_id}"):
            services[key] = own
    return HostSettings.model_validate(kept | {"services": services})


def _valid(model: type[BaseModel], field: str, given: Any, where: str) -> dict[str, Any]:
    """`{field: given}` when it validates as part of `model`, else nothing and a warning."""
    try:
        model.model_validate({field: given})
    except ValidationError as problem:
        log.warning("settings: stored %s, %s left out: %s", where, field, _why(problem))
        return {}
    return {field: given}


def _settings_key(config: HostConfig, service: Service) -> str:
    """The key the host's settings hold the service under: its own key, or the name the hosts file
    used for it, so a new override never hides the file's (#230); its own key when it has none."""
    own = service.id.removeprefix(f"{service.host_id}/")
    return next((k for k in (own, service.name) if k in config.services), own)


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
                "exits": over.exits if over.exits is not None else below.exits,
                "protected_containers": protected,
            }
        )
    data = base.model_dump() | {
        "rules": host.rules if host.rules is not None else base.rules,
        "may_sleep": host.may_sleep if host.may_sleep is not None else base.may_sleep,
        "services": {key: s.model_dump() for key, s in services.items()},
    }
    try:
        return HostConfig.model_validate(data)
    except ValidationError as error:
        # Only where, not the message: the hosts file's own messages quote the value.
        where = sorted({".".join(str(p) for p in e["loc"]) or "host" for e in error.errors()})
        raise SettingsRefused(
            f"host {base.id}: invalid {', '.join(where)}",
            "settings_host_invalid",
            host=base.id,
            where=", ".join(where),
        ) from None


def check_body(data: Any) -> None:
    """Raise `SettingsRefused` when a settings body (decoded JSON, not validated yet) carries
    anything secret-shaped, at any depth, in a key or a value."""
    _refuse_secrets(data)


def _refuse_secrets(data: Any) -> None:
    found = find_secrets(data, settings_field_names())
    if found:
        raise SettingsRefused(
            f"settings never hold secrets; {', '.join(found)} looks like one. "
            "Secrets go in the environment or a mounted file named in the hosts file",
            "settings_secret",
            found=", ".join(found),
        )


def _why(problem: Exception) -> str:
    """The reason without the input: a validation error's text would quote the value."""
    if isinstance(problem, ValidationError):
        return "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'value'}: {e['msg']}"
            for e in problem.errors(include_input=False, include_url=False)
        )
    return str(problem)
