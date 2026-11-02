from dataclasses import dataclass

from bleep.auth.sessions import LoginLimiter, SessionStore
from bleep.config import Settings
from bleep.hosts.config import load_hosts_file
from bleep.hosts.registry import Registry
from bleep.incidents.repository import IncidentRepo
from bleep.store.db import Database


@dataclass
class AppParts:
    """Everything the app runs on, built once from the settings."""

    db: Database
    registry: Registry
    incidents: IncidentRepo
    sessions: SessionStore
    login_limiter: LoginLimiter


def build_parts(settings: Settings) -> AppParts:
    """Build the parts; the database is migrated and the registry started by the app's lifespan."""
    db = Database(settings.data_dir / "bleep.db")
    hosts = load_hosts_file(settings.hosts_file).hosts
    return AppParts(
        db=db,
        registry=Registry(hosts),
        incidents=IncidentRepo(db),
        sessions=SessionStore(db),
        login_limiter=LoginLimiter(),
    )
