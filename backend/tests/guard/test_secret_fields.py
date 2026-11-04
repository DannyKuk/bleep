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


@pytest.mark.parametrize("name", ["ｔｏｋｅｎ", "pаssword", "pass​word", "to​ken", "ＡＰＩ_ＫＥＹ"])
def test_look_alike_and_invisible_characters_do_not_hide_a_secret_name(name):
    assert is_secret_field(name)


@pytest.mark.parametrize(
    "value",
    [
        "pаssword=hunter2xyz",  # Cyrillic а
        "pass​word=hunter2xyz",
        "ｐａｓｓｗｏｒｄ=hunter2xyz",
        "https://shop.example.test/h?key=hunter2xyz",
        "https://shop.example.test/h?a=1&api_key=hunter2xyz",
    ],
)
def test_look_alike_values_and_secret_query_parameters_are_found(value):
    assert find_secrets({"rules": [value]}) == ["rules[0]"]


def test_a_url_with_a_plain_query_passes():
    assert find_secrets({"url": "https://shop.example.test/health?full=1"}) == []


def test_a_deeply_nested_body_is_walked_without_recursion():
    body: dict = {"token": "x"}
    for _ in range(5_000):
        body = {"a": body}
    assert len(find_secrets(body)) == 1


def test_plain_text_over_4000_characters_in_short_lines_passes():
    text = "\n".join(["Restart the shop only after its database is healthy."] * 100)
    assert len(text) > 4_000
    assert find_secrets(text) == []


def test_a_look_alike_secret_on_one_line_of_many_is_found():
    text = "\n".join(["plain words here"] * 100 + ["pаssword=hunter2xyz"])
    assert find_secrets(text) == ["body"]
