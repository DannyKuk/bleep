from pydantic import SecretStr

from bleep.config import Settings


def test_health_ok(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_settings_read_the_environment(monkeypatch):
    monkeypatch.setenv("BLEEP_ADMIN_PASSWORD", "pw")
    monkeypatch.setenv("BLEEP_SECRET", "s")
    monkeypatch.setenv("BLEEP_HOSTS_FILE", "/tmp/h.yaml")
    settings = Settings()
    assert settings.admin_password == SecretStr("pw")
    assert settings.secret.get_secret_value() == "s"
    assert str(settings.hosts_file) == "/tmp/h.yaml"
    assert str(settings.data_dir) == "/data"


def test_secrets_do_not_show_in_repr(settings):
    assert "test-password" not in repr(settings)


def test_empty_env_values_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("BLEEP_ADMIN_PASSWORD", "pw")
    monkeypatch.setenv("BLEEP_SECRET", "s")
    monkeypatch.setenv("BLEEP_HOSTS_FILE", "")
    assert str(Settings().hosts_file) == "/config/hosts.yaml"
