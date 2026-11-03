from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import SecretStr

from bleep.auth.sessions import LoginLimiter, SessionStore
from bleep.config import Settings
from bleep.graph.deps import GraphDeps, Notifier, SettingsSource, Tracer, model_factory
from bleep.guard.log import GuardLog
from bleep.hosts.config import load_hosts_file
from bleep.hosts.github import GithubClient, github_token
from bleep.hosts.registry import Registry
from bleep.incidents.action_log import ActionLog
from bleep.incidents.approvals import ApprovalStore
from bleep.incidents.bus import EventBus
from bleep.incidents.prompt_overrides import PromptOverrideStore
from bleep.incidents.repository import IncidentRepo
from bleep.incidents.settings_store import SettingsStore
from bleep.knowledge.index import KnowledgeIndex
from bleep.knowledge.ingest import KnowledgeRefresher
from bleep.knowledge.repos import GithubRepoDocs
from bleep.llm.tracing import TracingSettings, build_tracer
from bleep.llm.usage import CostLedger
from bleep.memory.embeddings import (
    DEFAULT_LOCAL_MODEL,
    Embedder,
    FastEmbedEmbedder,
    OpenAICompatEmbedder,
)
from bleep.memory.store import MemoryStore
from bleep.notify.notifier import NotifySetup
from bleep.notify.notifier import setup as setup_notify
from bleep.plugins.base import Plugin
from bleep.plugins.registry import PluginRegistry
from bleep.store.db import Database
from bleep.tools.default import default_registry
from bleep.tools.docker_act import docker_act_tools


@dataclass
class AppParts:
    """Everything the app runs on, built once from the settings."""

    db: Database
    registry: Registry
    bus: EventBus
    graph_deps: GraphDeps
    incidents: IncidentRepo
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


# Every plugin Bleep has; each is off until a person switches it on.
PLUGINS: tuple[type[Plugin], ...] = ()


def build_embedder(settings: Settings) -> Embedder:
    """The local model unless the settings name an OpenAI-compatible endpoint (checked complete
    by `check_settings`). `EMBEDDINGS_MODEL` names the endpoint's model only: the local model is
    the one baked into the image, and another could not be downloaded there."""
    if settings.embeddings_provider == "openai":
        return OpenAICompatEmbedder(
            settings.embeddings_base_url or "",
            settings.embeddings_model or "",
            settings.embeddings_api_key,
        )
    return FastEmbedEmbedder(DEFAULT_LOCAL_MODEL)


def build_parts(
    settings: Settings,
    registry: Registry | None = None,
    embedder: Embedder | None = None,
    tracer: Tracer | None = None,
    plugins: Sequence[Plugin] | None = None,
) -> AppParts:
    """Build the parts; the lifespan migrates the database, loads the stored settings and starts
    the registry.

    `registry` replaces the one read from the hosts file (tests pass an empty or fake one),
    `embedder` the one the settings name (tests pass `FakeEmbedder`), `tracer` the one
    `TRACING` names, and `plugins` the plugins Bleep has (`PLUGINS`). The GitHub client reads
    with the hosts file's token, or without one when a registry is passed or no token is set.
    """
    db = Database(settings.data_dir / "bleep.db")
    github_config = None
    if registry is None:
        hosts_file = load_hosts_file(settings.hosts_file)
        registry = Registry(hosts_file.hosts)
        github_config = hosts_file.github
    github = GithubClient(github_token(github_config))
    bus, incidents = EventBus(db), IncidentRepo(db)
    approvals, guard_log = ApprovalStore(db, settings.secret), GuardLog(db)
    notify = setup_notify(settings, db, bus, approvals, guard_log)
    embedder = embedder or build_embedder(settings)  # one model, loaded once, for both
    memory = MemoryStore(db, embedder)
    knowledge = KnowledgeIndex(db, embedder)
    plugin_registry = PluginRegistry(
        [cls() for cls in PLUGINS] if plugins is None else plugins,
        settings.secret,
        guard_log=guard_log,
    )
    store = SettingsStore(db, registry, plugin_registry)
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
        ),
        incidents=incidents,
        approvals=approvals,
        guard_log=guard_log,
        sessions=SessionStore(db),
        login_limiter=LoginLimiter(),
        notify=notify,
        memory=memory,
        tracer=tracer or build_tracer(TracingSettings()),
        plugins=plugin_registry,
        settings_store=store,
        github=github,
        knowledge=knowledge,
        knowledge_refresher=KnowledgeRefresher(
            knowledge, reports=memory, repos=GithubRepoDocs(github, registry)
        ),
        prompt_overrides=PromptOverrideStore(db),
    )


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
        guard_log=guard_log,
        settings_store=settings_store,
        notifier=notifier,
        memory=memory,
    )
