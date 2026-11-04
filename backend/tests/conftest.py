import warnings

# The LangChain packages warn on import under Python 3.14 (pydantic.v1, a deprecated asyncio
# helper in langsmith). Import them once here with warnings silenced; pytest re-applies
# `-W error` per test module, so a filter would not hold.
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import langchain_core.messages  # noqa: F401
    import langchain_openai  # noqa: F401
    import langchain_openrouter  # noqa: F401

import os

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from bleep import prompts
from bleep.config import Settings
from bleep.hosts.registry import Registry
from bleep.main import create_app
from bleep.memory.embeddings import FakeEmbedder, FastEmbedEmbedder
from bleep.wiring import AppParts, build_parts


@pytest.fixture(autouse=True)
def no_tracing_from_the_shell(monkeypatch):
    """A `TRACING` exported to try the Langfuse profile must not make the tests send traces."""
    for name in ("TRACING", "LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def no_real_embedding_model(monkeypatch):
    """Tests never download or run the local model; one opt-in test does, with
    BLEEP_REAL_EMBEDDINGS=1."""
    if os.environ.get("BLEEP_REAL_EMBEDDINGS"):
        return

    def refuse(self):
        raise RuntimeError("tests use FakeEmbedder; the real model needs BLEEP_REAL_EMBEDDINGS=1")

    monkeypatch.setattr(FastEmbedEmbedder, "_load", refuse)


@pytest.fixture(autouse=True)
def no_name_lookups(monkeypatch):
    """Tests never resolve a name: a dashboard address checked without a fake resolver fails."""

    async def refuse(host: str, port: int) -> list[str]:
        raise OSError("tests do not resolve names; pass a resolver")

    monkeypatch.setattr("bleep.plugins.urls.resolve", refuse)


@pytest.fixture(autouse=True)
def no_prompt_overrides():
    """An override a test puts in force (the dev panel's) must not leak into the next test."""
    prompts.use_overrides({})
    yield
    prompts.use_overrides({})


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
    return build_parts(settings, Registry([]), embedder=FakeEmbedder())


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
    monkeypatch.setattr(prompts, "OVERRIDABLE", frozenset({"demo/hello"}))
    return root
