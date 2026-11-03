from dataclasses import dataclass

from pydantic import SecretStr

from bleep.auth.sessions import LoginLimiter, SessionStore
from bleep.config import Settings
from bleep.graph.deps import GraphDeps, Notifier, StaticSettings, model_factory
from bleep.guard.log import GuardLog
from bleep.hosts.config import load_hosts_file
from bleep.hosts.registry import Registry
from bleep.incidents.action_log import ActionLog
from bleep.incidents.approvals import ApprovalStore
from bleep.incidents.bus import EventBus
from bleep.incidents.repository import IncidentRepo
from bleep.llm.tracing import Tracer, TracingSettings, build_tracer
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
) -> AppParts:
    """Build the parts; the database is migrated and the registry started by the app's lifespan.

    `registry` replaces the one read from the hosts file (tests pass an empty or fake one),
    `embedder` the one the settings name (tests pass `FakeEmbedder`), and `tracer` the one
    `TRACING` names.
    """
    db = Database(settings.data_dir / "bleep.db")
    if registry is None:
        registry = Registry(load_hosts_file(settings.hosts_file).hosts)
    bus, incidents = EventBus(db), IncidentRepo(db)
    approvals, guard_log = ApprovalStore(db, settings.secret), GuardLog(db)
    notify = setup_notify(settings, db, bus, approvals, guard_log)
    memory = MemoryStore(db, embedder or build_embedder(settings))
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
        ),
        incidents=incidents,
        approvals=approvals,
        guard_log=guard_log,
        sessions=SessionStore(db),
        login_limiter=LoginLimiter(),
        notify=notify,
        memory=memory,
        tracer=tracer or build_tracer(TracingSettings()),
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
) -> GraphDeps:
    """What the incident graph runs on. Models are built when a node asks, so no key is needed.

    `approvals` is the store the API and the reminders use too, so a decision from any channel
    reaches the graph through one store. The act tools check its grants with the same
    `BLEEP_SECRET` it signs them with. `memory` is read by Gather and the `recall_incidents`
    tool and written by Report.
    """
    store = StaticSettings()
    return GraphDeps(
        registry=registry,
        tools=default_registry(memory),
        act_tools=docker_act_tools(secret, guard_log),
        models=model_factory(store),
        bus=bus,
        ledger=CostLedger(db),
        incidents=incidents,
        action_log=ActionLog(db),
        approvals=approvals,
        guard_log=guard_log,
        settings_store=store,
        notifier=notifier,
        memory=memory,
    )
