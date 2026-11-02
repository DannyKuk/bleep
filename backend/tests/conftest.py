import warnings

# The LangChain packages warn on import under Python 3.14 (pydantic.v1, a deprecated asyncio
# helper in langsmith). Import them once here with warnings silenced; pytest re-applies
# `-W error` per test module, so a filter would not hold.
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import langchain_core.messages  # noqa: F401
    import langchain_openai  # noqa: F401
    import langchain_openrouter  # noqa: F401

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from bleep import prompts
from bleep.auth.sessions import LoginLimiter, SessionStore
from bleep.config import Settings
from bleep.hosts.registry import Registry
from bleep.incidents.bus import EventBus
from bleep.incidents.repository import IncidentRepo
from bleep.main import create_app
from bleep.store.db import Database
from bleep.wiring import AppParts, graph_deps


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        hosts_file=tmp_path / "hosts.yaml",
        admin_password=SecretStr("test-password"),
        secret=SecretStr("test-secret"),
    )


@pytest.fixture
def parts(settings) -> AppParts:
    """App parts over a temp database and an empty registry: no hosts file, no real engine."""
    db = Database(settings.data_dir / "bleep.db")
    registry, bus = Registry([]), EventBus(db)
    return AppParts(
        db=db,
        registry=registry,
        bus=bus,
        graph_deps=graph_deps(db, registry, bus),
        incidents=IncidentRepo(db),
        sessions=SessionStore(db),
        login_limiter=LoginLimiter(),
    )


@pytest.fixture
def client(settings, parts):
    with TestClient(create_app(settings, parts)) as test_client:
        yield test_client


@pytest.fixture
def tmp_prompts(tmp_path, monkeypatch):
    """A temporary prompts directory with one demo prompt; the loader reads from it."""
    root = tmp_path / "prompts"
    (root / "demo").mkdir(parents=True)
    (root / "demo" / "hello.md").write_text("Hi {name} {{x}}", encoding="utf-8")
    monkeypatch.setattr(prompts, "PROMPTS_DIR", root)
    return root
