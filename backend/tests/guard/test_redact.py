import pytest

from bleep.guard.redact import redact_env, redact_text, redact_with_count

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
TELEGRAM = "123456789:AAH" + "a" * 32

SECRETS = [
    ("Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
    ("authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA"),
    ("curl -H 'Authorization: Token hunter2hunter2'", "hunter2hunter2"),
    ("using Bearer abcd1234efgh5678", "abcd1234efgh5678"),
    ("token ghp_" + "A1b2" * 9, "ghp_A1b2"),
    ("token gho_" + "A1b2" * 9, "gho_A1b2"),
    ("token github_pat_" + "A1b2_" * 9, "github_pat_A1b2"),
    ("key sk-" + "a1B2" * 12, "sk-a1B2"),
    ("key sk-or-v1-" + "a1B2" * 12, "sk-or-v1-a1B2"),
    (f"jwt {JWT}", "eyJhbGci"),
    ("postgres://app:s3cr3tpw@db:5432/app", "s3cr3tpw"),
    ("redis://:onlypw@cache:6379", "onlypw"),
    ("password=hunter2", "hunter2"),
    ("passwd=hunter2", "hunter2"),
    ("secret=hunter2", "hunter2"),
    ("token=hunter2", "hunter2"),
    ("api_key=hunter2", "hunter2"),
    ("apikey=hunter2", "hunter2"),
    ("DB_PASSWORD=hunter2 next", "hunter2"),
    ('{"password": "hunter2 with space"}', "hunter2"),
    ("{'api_key': 'hunter2'}", "hunter2"),
    ("password: hunter2", "hunter2"),
    ("client_secret: 'hunter2'", "hunter2"),
    ("key AKIAIOSFODNN7EXAMPLE here", "AKIAIOSFODNN7EXAMPLE"),
    (
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nabc\n-----END RSA PRIVATE KEY-----",
        "MIIEow",
    ),
    ("-----BEGIN PRIVATE KEY-----\nMIIEow\ncut off mid", "MIIEow"),
    (f"bot token {TELEGRAM} sent", "AAHaaaa"),
    (f"https://api.telegram.org/bot{TELEGRAM}/sendMessage", "AAHaaaa"),
]


@pytest.mark.parametrize(("text", "secret"), SECRETS)
def test_secret_is_redacted(text, secret):
    out = redact_text(text)
    assert secret not in out
    assert "<redacted>" in out
    assert redact_text(out) == out  # idempotent


def test_url_keeps_user():
    assert redact_text("postgres://app:s3cr3tpw@db/x") == "postgres://app:<redacted>@db/x"


def test_key_name_stays_visible():
    assert redact_text("password=hunter2 ok") == "password=<redacted> ok"
    assert redact_text('{"password": "x y"}') == '{"password": "<redacted>"}'


CONTROLS = [
    "request id 550e8400-e29b-41d4-a716-446655440000 done",
    "container " + "a1b2c3d4e5f6" * 5 + "abcd started",
    "image nginx@sha256:" + "ab12" * 16,
    "2026-11-02T10:00:00.123456Z INFO ready",
    "10:00:00 12:34:56",
    "password reset email sent",
    "Bearer authentication is enabled",
    "max_tokens=512 tokens used",
    "GET /api/v1/users?page=2 200 12ms",
    "see a.b.c for details",
    "sk-short",
    "connect to postgres://db:5432/app ok",
    "user@example.com logged in",
]


@pytest.mark.parametrize("text", CONTROLS)
def test_false_positive_controls_untouched(text):
    assert redact_text(text) == text
    assert redact_with_count(text) == (text, 0)


def test_count():
    text = "password=a token=b ghp_" + "A1b2" * 9
    out, count = redact_with_count(text)
    assert count == 3
    assert redact_with_count(out) == (out, 0)


def test_redact_env_hides_every_value():
    assert redact_env(["A=1", "B=", "C=x=y", "NOEQ"]) == [
        "A=<redacted>",
        "B=<redacted>",
        "C=<redacted>",
        "NOEQ",
    ]
