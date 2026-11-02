from dataclasses import dataclass

from bleep.auth.sessions import LoginLimiter, SessionStore
from bleep.config import Settings
from bleep.graph.deps import GraphDeps, StaticSettings, model_factory
from bleep.hosts.config import load_hosts_file
from bleep.hosts.registry import Registry
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
    sessions: SessionStore
    login_limiter: LoginLimiter


def build_parts(settings: Settings) -> AppParts:
    """Build the parts; the database is migrated and the registry started by the app's lifespan."""
    db = Database(settings.data_dir / "bleep.db")
    registry = Registry(load_hosts_file(settings.hosts_file).hosts)
    return AppParts(
        db=db,
        registry=registry,
        bus=(bus := EventBus(db)),
        graph_deps=graph_deps(db, registry, bus),
        incidents=IncidentRepo(db),
        sessions=SessionStore(db),
        login_limiter=LoginLimiter(),
    )


def graph_deps(db: Database, registry: Registry, bus: EventBus) -> GraphDeps:
    """What the incident graph runs on. Models are built when a node asks, so no key is needed."""
    store = StaticSettings()
    return GraphDeps(
        registry=registry,
        tools=default_registry(),
        models=model_factory(store),
        bus=bus,
        ledger=CostLedger(db),
        settings_store=store,
    )
