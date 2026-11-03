import pytest

from bleep.guard.secret_fields import find_secrets, is_secret_field


@pytest.mark.parametrize(
    "name",
    [
        "token", "TOKEN", "Token", "access_token", "apiKey", "API_KEY", "Api-Key", "x-api-key",
        "password", "DB_PASSWORD", "passwd", "pwd", "secret", "clientSecret", "key", "ssh_key",
        "private.key", "privateKey", "credentials", "Authorization", "cookie", "coolify_token",
        "token_file", "key_file",
    ],
)  # fmt: skip
def test_secret_shaped_names(name):
    assert is_secret_field(name)


@pytest.mark.parametrize(
    "name",
    ["url", "rules", "urls", "protected_containers", "model", "cheap_model", "compose:shop",
     "auth-service", "monkey", "keycloak", "body_contains", "expect_status", "team"],
)  # fmt: skip
def test_plain_names(name):
    assert not is_secret_field(name)


def test_finds_secret_keys_and_values_at_any_depth_and_names_only_the_path():
    body = {
        "budgets": {"incident_tokens": 1000},
        "hosts": {
            "a": {
                "rules": ["fine", "the key is sk-live-0123456789abcdef0123"],
                "services": {"s": {"urls": [{"url": "https://u:p4ssw0rd99@x.test/"}]}},
                "Nested": {"deeper": [{"Api_Key": "v"}]},
            }
        },
    }
    found = find_secrets(body, allowed={"incident_tokens"})
    assert found == [
        "hosts.a.rules[1]",
        "hosts.a.services.s.urls[0].url",
        "hosts.a.Nested.deeper[0].Api_Key",
    ]


def test_an_allowed_field_is_trusted_by_name_only():
    assert find_secrets({"incident_tokens": 5}, allowed={"incident_tokens"}) == []
    assert find_secrets({"incident_tokens": 5}) == ["incident_tokens"]


def test_plain_text_passes():
    assert find_secrets({"rules": ["never restart postgres without asking"]}) == []
