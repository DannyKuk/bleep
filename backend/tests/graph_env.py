"""A ready-made graph environment for tests: a temp database, a fake host and `GraphDeps`.

Every graph test (Gather, the runner, and the node tasks after them) builds on this, so no test
touches a real host, engine or model.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel

from bleep.graph.deps import GraphDeps
from bleep.hosts.config import HostConfig, ServiceOverrides
from bleep.hosts.registry import Registry
from bleep.incidents.bus import EventBus
from bleep.incidents.repository import IncidentRepo
from bleep.llm.usage import CostLedger
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.incidents import Incident, Signal, Subject
from bleep.store.db import Database
from bleep.tools.default import default_registry
from fakes import FakeChatModel, FakeEngine, FakeShell, ssh_host

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
COMPOSE_PROJECT = "com.docker.compose.project"


def box(name: str, project: str | None = None, **fields: Any) -> ContainerSummary:
    labels = {COMPOSE_PROJECT: project} if project else {}
    defaults: dict[str, Any] = {"image": "img:1", "state": "running", "labels": labels}
    return ContainerSummary(id=f"id-{name}", name=name, **(defaults | fields))


class RecordingGuardLog:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, str]] = []

    def record(self, incident_id: str, kind: str, detail: str) -> None:
        self.records.append((incident_id, kind, detail))


@dataclass
class CountingModels:
    """A model factory that hands out one `FakeChatModel` and counts every request for one."""

    model: FakeChatModel = field(default_factory=lambda: FakeChatModel(script=[]))
    calls: list[str] = field(default_factory=list)

    def __call__(self, role: str) -> BaseChatModel:
        self.calls.append(role)
        return self.model


@dataclass
class GraphEnv:
    db: Database
    repo: IncidentRepo
    bus: EventBus
    registry: Registry
    engine: FakeEngine
    shell: FakeShell
    guard_log: RecordingGuardLog
    models: CountingModels
    deps: GraphDeps

    def service_id(self, name: str) -> str:
        return next(s.id for s in self.registry.services("a") if s.name == name)

    async def open(self, subject: Subject, kind: str = "container_died", **signal: Any) -> Incident:
        incident, _ = await self.repo.open_or_attach(
            subject, [Signal(subject=subject, kind=kind, at=T0, **signal)]
        )
        return incident

    async def nodes(self, incident_id: str, kind: str = "node_started") -> list[str]:
        """The node names of `kind` events the incident published, in order."""
        return [e.payload["node"] for e in await self.bus.history(incident_id) if e.kind == kind]


def shop_host(**overrides: Any) -> HostConfig:
    fields: dict[str, Any] = {
        "id": "a",
        "rules": ["never restart postgres without telling me which app depends on it"],
        "services": {"shop": ServiceOverrides(rules=["the shop db is shared with billing"])},
    }
    return ssh_host(**(fields | overrides))


def shop_engine() -> FakeEngine:
    return FakeEngine(
        "a",
        [
            box("shop-web-1", "shop", state="exited", exit_code=1, restart_policy="no"),
            box("shop-db-1", "shop"),
            box("blog-1", "blog"),
        ],
    )


async def graph_env(
    tmp_path: Path,
    engine: FakeEngine | None = None,
    shell: FakeShell | None = None,
    host: HostConfig | None = None,
    bus: EventBus | None = None,
) -> GraphEnv:
    """Build the environment with the registry started (so services and basics are known)."""
    db = Database(tmp_path / "bleep.sqlite")
    await db.migrate()
    engine = engine or shop_engine()
    shell = shell or FakeShell("linux")

    async def connect(_host: HostConfig) -> tuple[FakeShell, FakeEngine]:
        return shell, engine

    registry = Registry([host or shop_host()], connect)  # type: ignore[arg-type]
    await registry.start()
    bus = bus or EventBus(db)
    guard_log = RecordingGuardLog()
    models = CountingModels()
    deps = GraphDeps(
        registry=registry,
        tools=default_registry(),
        models=models,
        bus=bus,
        ledger=CostLedger(db),
        guard_log=guard_log,
    )
    return GraphEnv(db, IncidentRepo(db), bus, registry, engine, shell, guard_log, models, deps)
