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
from bleep.config import Settings
from bleep.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        hosts_file=tmp_path / "hosts.yaml",
        admin_password=SecretStr("test-password"),
        secret=SecretStr("test-secret"),
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def tmp_prompts(tmp_path, monkeypatch):
    """A temporary prompts directory with one demo prompt; the loader reads from it."""
    root = tmp_path / "prompts"
    (root / "demo").mkdir(parents=True)
    (root / "demo" / "hello.md").write_text("Hi {name} {{x}}", encoding="utf-8")
    monkeypatch.setattr(prompts, "PROMPTS_DIR", root)
    return root
