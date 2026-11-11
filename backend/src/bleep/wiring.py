import functools
import logging
import os
from collections import ChainMap
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from pydantic import SecretStr

from bleep.auth.sessions import LoginLimiter, SessionStore
from bleep.config import Settings, SettingsError
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
    DeferredEmbedder,
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
from bleep.schemas.settings import (
    EMBEDDINGS_URL_ENV,
    ENV_NAMES,
    LOCAL_URL_ENV,
    MODEL_KEY_ENV,
    TRACING_URLS,
    EmbeddingsChoice,
    EmbeddingsOverrides,
    TracingOverrides,
    has_userinfo,
)
from bleep.store.db import Database
from bleep.store.secrets import SecretName, SecretStore
from bleep.talk.conversation import Talk, Tracing
from bleep.talk.panel import Panel
from bleep.talk.state import BleepState
from bleep.tools.default import default_registry
from bleep.tools.docker_act import docker_act_tools
from bleep.watcher.health_store import HealthStore
from bleep.watcher.http_checks import UrlCheckStore
from bleep.watcher.overview import Overview


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
    talk: Talk
    panel: Panel  # the dashboard's own conversation, read with `talk`


log = logging.getLogger(__name__)

# Every plugin Bleep has; each is off until a person switches it on.
PLUGINS: tuple[type[Plugin], ...] = (CoolifyPlugin, McpUrlPlugin)
GITHUB_TOKEN_ENV = "GITHUB_TOKEN"
EMBEDDINGS_KEY_ENV = "EMBEDDINGS_API_KEY"
SECRET_FILE = "secrets.json"


def secret_names(plugins: Sequence[Plugin], tracing: str) -> list[SecretName]:
    """Every value the dashboard may set. `BLEEP_ADMIN_PASSWORD` and `BLEEP_SECRET` are not
    among them: the dashboard is behind them. An address here is bound to the token it carries:
    a plugin's, and a model server's (#372). The tracers' addresses are settings, held to their
    keys by the settings store."""
    needed = TRACING_KEYS.get(tracing, ())
    names = [
        SecretName(MODEL_KEY_ENV),
        SecretName(TELEGRAM_TOKEN_ENV),
        SecretName(CHAT_ID_ENV, kind="id"),
        SecretName(GITHUB_TOKEN_ENV),
        # A model server's addresses may be private (a local model, #372); the embeddings
        # endpoint's carries its key, cleared when it changes. The local model takes no key.
        SecretName(LOCAL_URL_ENV, kind="address", private=True),
        SecretName(EMBEDDINGS_URL_ENV, kind="address", carries=EMBEDDINGS_KEY_ENV, private=True),
        SecretName(EMBEDDINGS_KEY_ENV),
        *(
            SecretName(key, required=tracing if key in needed else None)
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
        EMBEDDINGS_URL_ENV: settings.embeddings_base_url,
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


def embeddings_env(settings: Settings) -> EmbeddingsOverrides:
    """The embeddings fields the env sets, which win over the settings page."""
    given = {
        "provider": settings.embeddings_provider,
        "model": settings.embeddings_model,
        "min_score": settings.embeddings_min_score,
    }
    chosen = {f"embeddings_{field}" for field in given} & settings.model_fields_set
    # Not validated as a page's body is: the env's model name is the operator's, whatever it is.
    return EmbeddingsOverrides.model_construct(
        **{field: value for field, value in given.items() if f"embeddings_{field}" in chosen}
    )


def tracing_env() -> TracingOverrides:
    """The tracing fields the env sets (`TRACING`, the addresses, the projects), which win. An
    address with a user or password in it stops Bleep, without quoting it: the API would show it,
    and the clients would send it on."""
    env = TracingSettings()
    fields = env.model_fields_set & set(TracingOverrides.model_fields)
    for field in sorted(fields & set(TRACING_URLS)):
        if has_userinfo(getattr(env, field) or ""):
            raise SettingsError(
                f"{ENV_NAMES['tracing'][field]} must have no user or password in it; "
                "remove them from .env"
            )
    return TracingOverrides.model_construct(**{field: getattr(env, field) for field in fields})


def build_embedder(choice: EmbeddingsChoice, secrets: SecretStore) -> Embedder:
    """The built-in model unless the choice names an OpenAI-compatible endpoint, or a local
    server, whose model and base URL are set; one without them falls back to the built-in model,
    with a warning. The model names the endpoint's or the server's model only: the built-in model
    is the one baked into the image, and another could not be downloaded there. The address is
    read from the secret store at each call, and so is an endpoint's key; a local server is sent
    none. `min_score` sets recall's cut-off; unset, the model's own."""
    if choice.provider in ("openai", "server"):
        if choice.model and secrets.get(EMBEDDINGS_URL_ENV) is not None:
            return OpenAICompatEmbedder(
                lambda: _value(secrets.get(EMBEDDINGS_URL_ENV)),
                choice.model,
                (lambda: secrets.get(EMBEDDINGS_KEY_ENV)) if choice.provider == "openai" else None,
                min_score=choice.min_score,
                kind=choice.provider,
            )
        log.warning(
            "embeddings: an endpoint or a local server needs EMBEDDINGS_BASE_URL and "
            "EMBEDDINGS_MODEL; the built-in model embeds until both are set and Bleep is restarted"
        )
    return FastEmbedEmbedder(DEFAULT_LOCAL_MODEL, min_score=choice.min_score)


def _value(secret: SecretStr | None) -> str | None:
    return secret.get_secret_value() if secret is not None else None


def start_embedder(store: SettingsStore, secrets: SecretStore) -> Embedder:
    """The embedder the settings in force name, recorded in the store so a change saved later
    shows as needing a restart. An endpoint or a server that is not complete is the built-in
    model."""
    choice = store.start_embeddings()
    if choice.provider == "local" and store.embeddings().provider != "local":
        log.warning(
            "embeddings: an endpoint or a local server needs EMBEDDINGS_BASE_URL and "
            "EMBEDDINGS_MODEL; the built-in model embeds until both are set and Bleep is restarted"
        )
    return build_embedder(choice, secrets)


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
    plugin_registry = PluginRegistry(
        plugins,
        settings.secret,
        guard_log=guard_log,
        env=secrets.environ(),
        from_dashboard=lambda name: secrets.state(name).source == "dashboard",
        resolver=resolver,
    )
    store = SettingsStore(
        db, registry, plugin_registry, secrets=secrets, dashboard=dashboard_hosts,
        timezone=settings.timezone, embeddings=embeddings_env(settings), tracing=tracing_env(),
    )  # fmt: skip
    url_checks = UrlCheckStore(db)
    overview = Overview(registry, incidents, quiet, url_checks)
    # Before `talk`, which traces its readings. Built from the env here, and again once the
    # stored settings are loaded (`subscribe_reloads`).
    tracer = tracer or ReloadingTracer(secrets.get, store.tracing)
    reads = tracer if isinstance(tracer, Tracing) else None  # a tracer may trace incidents only
    talk = build_talk(settings, db, overview, incidents, approvals, bus, store, guard_log, reads)
    notify = setup_notify(
        settings,
        db,
        bus,
        approvals,
        guard_log,
        secrets=secrets,
        incidents=incidents,
        quiet=quiet,
        zone=store.zone,  # the settings page's choice, read at each message
        language=store.language,  # the same, so a change applies to the next message
        talk=talk,
    )
    incidents.on_finished(notify.notifier.incident_finished)
    approvals.on_decided(notify.notifier.approval_decided)
    # One model, loaded once, for both; built at its first use, after the stored settings load.
    embedder = embedder or DeferredEmbedder(lambda: start_embedder(store, secrets))
    memory = MemoryStore(db, embedder)
    knowledge = KnowledgeIndex(db, embedder)
    incidents.fold_window_h = lambda: store.thresholds().fold_window_h
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
        url_checks=url_checks,
        service_health=HealthStore(db),
        secrets=secrets,
        dashboard_hosts=dashboard_hosts,
        talk=talk,
        panel=Panel(talk),
    )


def build_talk(
    settings: Settings,
    db: Database,
    overview: Overview,
    incidents: IncidentRepo,
    approvals: ApprovalStore,
    bus: EventBus,
    store: SettingsStore,
    guard_log: GuardLog,
    tracer: Tracing | None = None,
) -> Talk:
    """The conversation both surfaces answer with: Telegram's bot and the dashboard's
    panel. It reads the watcher's overview as its health, the cheap model from the settings in
    force, and counts its calls in the usage ledger under the daily cap. `tracer` traces each
    reading, in its surface's session."""
    state = BleepState(overview, incidents, approvals, bus, zone=store.zone)
    return Talk(
        state,
        model_factory(store),
        budgets=store.budgets,
        ledger=CostLedger(db),
        guard_log=guard_log,
        zone=store.zone,
        language=store.language,
        tracer=tracer,
    )


def subscribe_reloads(
    secrets: SecretStore, notify: NotifySetup, store: SettingsStore, tracer: Tracer
) -> None:
    """After a change on the dashboard, restart only the part that holds the value: the Telegram
    poller, a plugin, the tracer (also after a change of the tracing settings, and once they are
    loaded). The model key and base URL, the GitHub token and the embeddings key and address
    need nothing: they are read from the store at each use."""
    telegram = {TELEGRAM_TOKEN_ENV, CHAT_ID_ENV}
    tracing = {key for keys in TRACING_KEYS.values() for key in keys}

    async def reload_telegram(changed: frozenset[str]) -> None:
        if changed & telegram:
            await notify.reload()

    async def reload_tracer(changed: frozenset[str]) -> None:
        if changed & tracing and isinstance(tracer, ReloadingTracer):
            await tracer.reload()

    async def tracing_changed(sections: frozenset[str]) -> None:
        if "tracing" in sections and isinstance(tracer, ReloadingTracer):
            await tracer.reload()

    secrets.subscribe(store.secrets_changing, before_write=True)
    for listener in (reload_telegram, reload_tracer, store.secrets_changed):
        secrets.subscribe(listener)
    store.subscribe(tracing_changed)
    if isinstance(tracer, ReloadingTracer):
        store.restart_when("tracing", tracer.restart_needed)


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
