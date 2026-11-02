import pytest
from langchain_openai import ChatOpenAI
from langchain_openrouter import ChatOpenRouter

from bleep.llm.factory import chat_model
from bleep.schemas.settings import Budgets, ModelSettings


def test_defaults():
    settings = ModelSettings()
    assert settings.provider == "openrouter"
    assert settings.model == settings.cheap_model == "openai/gpt-6-luna"
    assert settings.local_base_url == "http://host.docker.internal:1234/v1"
    assert settings.local_model == "openai/gpt-oss-20b"
    assert settings.temperature == 0


def test_env_names_have_no_prefix(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "local")
    monkeypatch.setenv("LLM_CHEAP_MODEL", "small/one")
    settings = ModelSettings()
    assert settings.provider == "local"
    assert settings.cheap_model == "small/one"


def test_budget_defaults():
    budgets = Budgets()
    assert budgets.investigate_tool_calls == 8
    assert budgets.low_confidence == 0.6
    assert budgets.verify_loops == 2
    assert budgets.incident_tokens == 150_000
    assert budgets.daily_cost_usd == 1.0


def test_local_provider_builds_chat_openai_with_base_url():
    settings = ModelSettings(provider="local", local_base_url="http://localhost:9/v1")
    model = chat_model(settings, "main")
    assert isinstance(model, ChatOpenAI)
    assert model.openai_api_base == "http://localhost:9/v1"
    assert model.model_name == "openai/gpt-oss-20b"
    assert model.temperature == 0


def test_openrouter_roles_pick_models(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    settings = ModelSettings(model="big/model", cheap_model="small/model")
    main = chat_model(settings, "main")
    cheap = chat_model(settings, "cheap")
    assert isinstance(main, ChatOpenRouter)
    assert main.model_name == "big/model"
    assert cheap.model_name == "small/model"


def test_unknown_role_rejected():
    with pytest.raises(ValueError):
        chat_model(ModelSettings(provider="local"), "huge")  # type: ignore[arg-type]
