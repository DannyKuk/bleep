import functools
import os
from collections import ChainMap
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from pydantic import SecretStr

from bleep.auth.sessions import LoginLimiter, SessionStore
from bleep.config import Settings
from bleep.graph.deps import (
    GraphDeps,
    NeverQuiet,
    Notifier,
    QuietCheck,
    SettingsSource,
    Tracer,
    model_factory,
)
from bleep.guard.log import GuardLog
from bleep.hosts.config import GithubConfig, load_hosts_file
from bleep.hosts.dashboard import HOST_KEYS, SSH_KEYS, DashboardHosts, dashboard_keys
from bleep.hosts.github import GithubClient, github_token
from bleep.hosts.registry import Registry, connect_host
from bleep.hosts.ssh import Scan
from bleep.incidents.action_log import ActionLog
from bleep.incidents.approvals import ApprovalStore
from bleep.incidents.bus import EventBus
from bleep.incidents.prompt_overrides import PromptOverrideStore
from bleep.incidents.quiet import QuietStore
from bleep.incidents.repository import IncidentRepo
from bleep.incidents.settings_store import SettingsStore
from bleep.knowledge.index import KnowledgeIndex
from bleep.knowledge.ingest import KnowledgeRefresher
from bleep.knowledge.repos import GithubRepoDocs
from bleep.llm.tracing import TRACING_KEYS, ReloadingTracer, TracingSettings
from bleep.llm.usage import CostLedger
from bleep.memory.embeddings import (
    DEFAULT_LOCAL_MODEL,
    Embedder,
    FastEmbedEmbedder,
    OpenAICompatEmbedder,
)
from bleep.memory.store import MemoryStore
from bleep.notify.notifier import CHAT_ID_ENV, NotifySetup
from bleep.notify.notifier import TOKEN_ENV as TELEGRAM_TOKEN_ENV
from bleep.notify.notifier import setup as setup_notify
from bleep.plugins.base import Plugin
from bleep.plugins.coolify.plugin import CoolifyPlugin
from bleep.plugins.mcp_url import McpUrlPlugin
from bleep.plugins.registry import PluginRegistry
from bleep.plugins.urls import Resolver
from bleep.schemas.settings import MODEL_KEY_ENV
from bleep.store.db import Database
from bleep.store.secrets import SecretName, SecretStore
from bleep.tools.default import default_registry
from bleep.tools.docker_act import docker_act_tools
from bleep.watcher.health_store import HealthStore
from bleep.watcher.http_checks import UrlCheckStore


@dataclass
class AppParts:
    """Everything the app runs on, built once from the settings."""

    db: Database
    registry: Registry
    bus: EventBus
    graph_deps: GraphDeps
    incidents: IncidentRepo
    quiet: QuietStore
    approvals: ApprovalStore
    guard_log: GuardLog
    sessions: SessionStore
    login_limiter: LoginLimiter
    notify: NotifySetup
    memory: MemoryStore
    tracer: Tracer
    plugins: PluginRegistry
    settings_store: SettingsStore
    github: GithubClient
    knowledge: KnowledgeIndex
    knowledge_refresher: KnowledgeRefresher
    prompt_overrides: PromptOverrideStore
    url_checks: UrlCheckStore
    service_health: HealthStore
    secrets: SecretStore
    dashboard_hosts: DashboardHosts


# Every plugin Bleep has; each is off until a person switches it on.
PLUGINS: tuple[type[Plugin], ...] = (CoolifyPlugin, McpUrlPlugin)
GITHUB_TOKEN_ENV = "GITHUB_TOKEN"
EMBEDDINGS_KEY_ENV = "EMBEDDINGS_API_KEY"
SECRET_FILE = "secrets.json"


def secret_names(plugins: Sequence[Plugin], tracing: str) -> list[SecretName]:
    """Every value the dashboard may set. `BLEEP_ADMIN_PASSWORD` and `BLEEP_SECRET` are not
    among them: the dashboard is behind them. Nor is any address but a plugin's: an address that
    could be changed from the dashboard decides where its token goes, and only a plugin's is
    bound to its token."""
    needed = TRACING_KEYS.get(tracing, ())
    names = [
        SecretName(MODEL_KEY_ENV),
        SecretName(TELEGRAM_TOKEN_ENV),
        SecretName(CHAT_ID_ENV, kind="id"),
        SecretName(GITHUB_TOKEN_ENV),
        SecretName(EMBEDDINGS_KEY_ENV),
        *(
            SecretName(key, required=f"TRACING={tracing} needs it" if key in needed else None)
            for keys in TRACING_KEYS.values()
            for key in keys
        ),
    ]
    for plugin in plugins:
        address, token = plugin.connection or (None, None)
        for env in (*plugin.secrets, *plugin.optional_secrets):
            if env not in {n.name for n in names}:
                carries = token if env == address else None
                names.append(
                    SecretName(env, kind="address" if carries else "secret", carries=carries)
                )
    return names


def secret_env(settings: Settings) -> Mapping[str, str]:
    """The environment the secret store reads first: the process's, live, and the values
    `Settings` was given (the same in production; tests build `Settings` with them)."""
    given = {
        TELEGRAM_TOKEN_ENV: settings.telegram_bot_token,
        CHAT_ID_ENV: settings.telegram_chat_id,
        EMBEDDINGS_KEY_ENV: settings.embeddings_api_key,
    }
    shown = {
        name: value if isinstance(value, str) else value.get_secret_value()
        for name, value in given.items()
        if value is not None
    }
    return ChainMap(os.environ, shown)  # type: ignore[arg-type]


def github_token_source(
    config: GithubConfig | None, secrets: SecretStore
) -> SecretStr | Callable[[], SecretStr | None] | None:
    """The hosts file's GitHub token when it names a file or another variable; else
    `GITHUB_TOKEN` as the secret store has it (the env, else the dashboard), read at each use."""
    if config is None or config.token.env == GITHUB_TOKEN_ENV:
        return lambda: secrets.get(GITHUB_TOKEN_ENV)
    return github_token(config)


def build_embedder(settings: Settings, secrets: SecretStore | None = None) -> Embedder:
    """The local model unless the settings name an OpenAI-compatible endpoint (checked complete
    by `check_settings`). `EMBEDDINGS_MODEL` names the endpoint's model only: the local model is
    the one baked into the image, and another could not be downloaded there. With `secrets`, the
    endpoint's key is read from there at each call."""
    if settings.embeddings_provider == "openai":
        key = settings.embeddings_api_key
        return OpenAICompatEmbedder(
            settings.embeddings_base_url or "",
            settings.embeddings_model or "",
            (lambda: secrets.get(EMBEDDINGS_KEY_ENV)) if secrets is not None else key,
        )
    return FastEmbedEmbedder(DEFAULT_LOCAL_MODEL)


def build_parts(
    settings: Settings,
    registry: Registry | None = None,
    embedder: Embedder | None = None,
    tracer: Tracer | None = None,
    plugins: Sequence[Plugin] | None = None,
    resolver: Resolver | None = None,
    scan: Scan | None = None,
) -> AppParts:
    """Build the parts; the lifespan migrates the database, loads the stored settings and starts
    the registry.

    `registry` replaces the one read from the hosts file (tests pass an empty or fake one),
    `embedder` the one the settings name (tests pass `FakeEmbedder`), `tracer` the one
    `TRACING` names, `plugins` the plugins Bleep has (`PLUGINS`), and `resolver` the name lookup
    a dashboard-set plugin address or a dashboard host's address is checked with (tests pass
    one; never the network), and `scan` the fetch of a host key offered for a host added on
    the dashboard (tests pass a fake). The
    GitHub client reads with the token `github_token_source` picks (no hosts file is read when a
    registry is passed).
    The secret store reads `data_dir/secrets.json`; a change there reloads only the part that
    holds the value (`subscribe_reloads`).
    """
    db = Database(settings.data_dir / "bleep.db")
    plugins = [cls() for cls in PLUGINS] if plugins is None else plugins
    secrets = SecretStore(
        settings.data_dir / SECRET_FILE,
        secret_names(plugins, TracingSettings().tracing),
        families=(SSH_KEYS, HOST_KEYS),
        env=secret_env(settings),
    )
    github_config = None
    if registry is None:
        hosts_file = load_hosts_file(settings.hosts_file)
        connect = functools.partial(connect_host, keys=dashboard_keys(secrets), resolve=resolver)
        registry = Registry(hosts_file.hosts, connect)
        github_config = hosts_file.github
    dashboard_hosts = DashboardHosts(
        db, secrets, [h.config.id for h in registry.hosts()], resolve=resolver
    )
    if scan is not None:
        dashboard_hosts.scan = scan
    github = GithubClient(github_token_source(github_config, secrets))
    bus, incidents, quiet = EventBus(db), IncidentRepo(db), QuietStore(db)
    approvals, guard_log = ApprovalStore(db, settings.secret), GuardLog(db)
    notify = setup_notify(settings, db, bus, approvals, guard_log, secrets=secrets)
    notify.notifier.incidents = incidents
    incidents.on_finished(notify.notifier.incident_finished)
    approvals.on_decided(notify.notifier.approval_decided)
    embedder = embedder or build_embedder(settings, secrets)  # one model, loaded once, for both
    memory = MemoryStore(db, embedder)
    knowledge = KnowledgeIndex(db, embedder)
    plugin_registry = PluginRegistry(
        plugins,
        settings.secret,
        guard_log=guard_log,
        env=secrets.environ(),
        from_dashboard=lambda name: secrets.state(name).source == "dashboard",
        resolver=resolver,
    )
    store = SettingsStore(db, registry, plugin_registry, secrets=secrets, dashboard=dashboard_hosts)
    incidents.fold_window_h = lambda: store.thresholds().fold_window_h
    tracer = tracer or ReloadingTracer(secrets.get)
    subscribe_reloads(secrets, notify, store, tracer)
    return AppParts(
        db=db,
        registry=registry,
        bus=bus,
        graph_deps=graph_deps(
            settings.secret,
            db,
            registry,
            bus,
            incidents,
            approvals,
            guard_log,
            notify.notifier,
            memory,
            store,
            plugin_registry,
            github,
            knowledge,
            quiet,
        ),
        incidents=incidents,
        quiet=quiet,
        approvals=approvals,
        guard_log=guard_log,
        sessions=SessionStore(db),
        login_limiter=LoginLimiter(),
        notify=notify,
        memory=memory,
        tracer=tracer,
        plugins=plugin_registry,
        settings_store=store,
        github=github,
        knowledge=knowledge,
        knowledge_refresher=KnowledgeRefresher(
            knowledge, reports=memory, repos=GithubRepoDocs(github, registry)
        ),
        prompt_overrides=PromptOverrideStore(db, guard_log),
        url_checks=UrlCheckStore(db),
        service_health=HealthStore(db),
        secrets=secrets,
        dashboard_hosts=dashboard_hosts,
    )


def subscribe_reloads(
    secrets: SecretStore, notify: NotifySetup, store: SettingsStore, tracer: Tracer
) -> None:
    """After a change on the dashboard, restart only the part that holds the value: the Telegram
    poller, a plugin, the tracer. The model key, the GitHub token and the embeddings key need
    nothing: they are read from the store at each use."""
    telegram = {TELEGRAM_TOKEN_ENV, CHAT_ID_ENV}
    tracing = {key for keys in TRACING_KEYS.values() for key in keys}

    async def reload_telegram(changed: frozenset[str]) -> None:
        if changed & telegram:
            await notify.reload()

    async def reload_tracer(changed: frozenset[str]) -> None:
        if changed & tracing and isinstance(tracer, ReloadingTracer):
            await tracer.reload()

    secrets.subscribe(store.secrets_changing, before_write=True)
    for listener in (reload_telegram, reload_tracer, store.secrets_changed):
        secrets.subscribe(listener)


def graph_deps(
    secret: SecretStr,
    db: Database,
    registry: Registry,
    bus: EventBus,
    incidents: IncidentRepo,
    approvals: ApprovalStore,
    guard_log: GuardLog,
    notifier: Notifier,
    memory: MemoryStore,
    settings_store: SettingsSource,
    plugins: PluginRegistry,
    github: GithubClient | None = None,
    knowledge: KnowledgeIndex | None = None,
    quiet: QuietCheck | None = None,
) -> GraphDeps:
    """What the incident graph runs on. Models are built when a node asks, so no key is needed.

    `approvals` is the store the API and the reminders use too, so a decision from any channel
    reaches the graph through one store. The act tools check its grants with the same
    `BLEEP_SECRET` it signs them with. `memory` is read by Gather and the `recall_incidents`
    tool and written by Report. The switched-on `plugins` add their read tools and act handlers,
    read at each use, and `settings_store` their actions to the plan check's enabled set.
    With `github`, a service with a `repo` gets the GitHub tools; `knowledge` is searched by the
    `knowledge_search` tool.
    """
    return GraphDeps(
        registry=registry,
        tools=default_registry(memory, plugins, github, knowledge),
        act_tools=plugins.act_tools(docker_act_tools(secret, guard_log)),
        models=model_factory(settings_store),
        bus=bus,
        ledger=CostLedger(db),
        incidents=incidents,
        action_log=ActionLog(db),
        approvals=approvals,
        quiet=quiet or NeverQuiet(),
        guard_log=guard_log,
        settings_store=settings_store,
        notifier=notifier,
        memory=memory,
    )
