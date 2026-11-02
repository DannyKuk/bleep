from dataclasses import dataclass

from bleep.auth.sessions import LoginLimiter, SessionStore
from bleep.config import Settings
from bleep.graph.deps import GraphDeps, StaticSettings, model_factory
from bleep.guard.log import GuardLog
from bleep.hosts.config import load_hosts_file
from bleep.hosts.registry import Registry
from bleep.incidents.approvals import ApprovalStore
from bleep.incidents.bus import EventBus
from bleep.incidents.repository import IncidentRepo
from bleep.llm.usage import CostLedger
from bleep.store.db import Database
from bleep.tools.default import default_registry


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


def build_parts(settings: Settings, registry: Registry | None = None) -> AppParts:
    """Build the parts; the database is migrated and the registry started by the app's lifespan.

    `registry` replaces the one read from the hosts file (tests pass an empty or fake one).
    """
    db = Database(settings.data_dir / "bleep.db")
    if registry is None:
        registry = Registry(load_hosts_file(settings.hosts_file).hosts)
    bus, incidents = EventBus(db), IncidentRepo(db)
    approvals, guard_log = ApprovalStore(db, settings.secret), GuardLog(db)
    return AppParts(
        db=db,
        registry=registry,
        bus=bus,
        graph_deps=graph_deps(db, registry, bus, incidents, approvals, guard_log),
        incidents=incidents,
        approvals=approvals,
        guard_log=guard_log,
        sessions=SessionStore(db),
        login_limiter=LoginLimiter(),
    )


def graph_deps(
    db: Database,
    registry: Registry,
    bus: EventBus,
    incidents: IncidentRepo,
    approvals: ApprovalStore,
    guard_log: GuardLog,
) -> GraphDeps:
    """What the incident graph runs on. Models are built when a node asks, so no key is needed.

    `approvals` is the store the API and the reminders use too, so a decision from any channel
    reaches the graph through one store.
    """
    store = StaticSettings()
    return GraphDeps(
        registry=registry,
        tools=default_registry(),
        models=model_factory(store),
        bus=bus,
        ledger=CostLedger(db),
        incidents=incidents,
        approvals=approvals,
        guard_log=guard_log,
        settings_store=store,
    )
