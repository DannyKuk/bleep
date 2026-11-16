import pytest

from bleep.config import Settings
from bleep.llm.tracing import TracingSettings
from bleep.schemas.settings import ModelSettings
from tests.settings_env import SETTINGS_CLASSES, clear_settings_env, names_read


def test_settings_read_no_dotenv_file():
    for cls in SETTINGS_CLASSES:
        assert cls.model_config.get("env_file") is None


def test_the_names_cover_every_spelling_the_classes_read():
    assert {"bleep_login_enabled", "bleep_secret", "telegram_bot_token"} <= names_read(Settings)
    assert {"embeddings_model", "embeddings_api_key"} <= names_read(Settings)
    assert {"llm_provider", "provider", "temperature"} <= names_read(ModelSettings)
    assert {"tracing", "langfuse_secret_key"} <= names_read(TracingSettings)


@pytest.mark.parametrize(
    ("name", "value", "read"),
    [
        ("BLEEP_LOGIN_ENABLED", "false", lambda: Settings(secret="s").login_enabled),
        ("Bleep_Watch_Interval_S", "3.5", lambda: Settings(secret="s").watch_interval_s),
        ("TELEGRAM_CHAT_ID", "9", lambda: Settings(secret="s").telegram_chat_id),
        ("LLM_PROVIDER", "anthropic", lambda: ModelSettings().provider),
        ("TEMPERATURE", "0.9", lambda: ModelSettings().temperature),
        ("TRACING", "langfuse", lambda: TracingSettings().tracing),
    ],
)
def test_an_exported_value_is_cleared_before_settings_read_it(monkeypatch, name, value, read):
    monkeypatch.setenv(name, value)
    assert str(read()).lower() == value.lower()  # the leak is real without the clearing

    clear_settings_env(monkeypatch)

    assert str(read()).lower() != value.lower()
