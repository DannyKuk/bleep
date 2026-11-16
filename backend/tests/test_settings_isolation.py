import os

import pytest

from bleep.config import Settings


def test_settings_read_no_dotenv_file():
    assert Settings.model_config.get("env_file") is None


def test_every_setting_name_is_cleared():
    """The autouse fixture ran before this body, so nothing it names may be in the environment."""
    from tests.conftest import _names_settings_reads

    names = {name.upper() for name in _names_settings_reads()}
    assert {"BLEEP_LOGIN_ENABLED", "BLEEP_SECRET", "TELEGRAM_BOT_TOKEN"} <= names
    assert "EMBEDDINGS_MODEL" in names
    assert not [name for name in os.environ if name.upper() in names]


@pytest.mark.parametrize("name", ["BLEEP_LOGIN_ENABLED", "BLEEP_WATCH_INTERVAL_S"])
def test_exported_value_does_not_reach_settings(name, settings):
    assert settings.login_enabled is True
    assert settings.watch_interval_s == 15
