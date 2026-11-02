import pytest

from bleep.guard.redact import redact_env, redact_text, redact_with_count

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
TELEGRAM = "123456789:AAH" + "a" * 32

SECRETS = [
    ("SECRET_KEY_BASE=8f3a9c2e1d", "8f3a9c2e1d"),
    ("STRIPE_SECRET_KEY_LIVE=sk_live_abc", "sk_live_abc"),
    ("REDIS_PASSWORD_PROD=hunter2", "hunter2"),
    ("API_KEY_ID=abc123", "abc123"),
    ("ENCRYPTION_KEY=abc999", "abc999"),
    ("SIGNING_KEY=abc999", "abc999"),
    ("MASTER_KEY=abc999", "abc999"),
    ("secretKey: abc999", "abc999"),
    ("apiKey=abc999", "abc999"),
    ("accessToken=abc999", "abc999"),
    ("password=ab'cdef", "cdef"),
    ('password=ab"cdef', "cdef"),
    ("postgres://u:pa/ss@db/x", "pa/ss"),
    ("postgres://u:p@ss@db/x?email=a@b.c", "p@ss"),
    ("password=ghp_" + "A" * 20 + "!tail", "tail"),
    ("password=<redacted>REALSECRET", "REALSECRET"),
    ("password: |\n  hunter2\n  second line", "second line"),
    ("password: >-\n  hunter2", "hunter2"),
    ("password:\n\n  hunter2", "hunter2"),
    ("Cookie: session=abcdef123456; theme=dark", "abcdef123456"),
    ("Set-Cookie: sid=abcdef123456; Path=/; HttpOnly", "abcdef123456"),
    ("hash $argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQ$RdescudvJCsgt3ub", "RdescudvJCsg"),
    ("  -----BEGIN RSA PRIVATE KEY-----\n  MIIEowIBAAKCAQEA\n  cut", "MIIEow"),
    (
        "key: |\n  "
        + "\n  ".join(["aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bC1dE3fG5hI7jK9lM1nO3pQ5rS7tU9vW1xY3zA5b"] * 3),
        "aB3dE5gH",
    ),
    (
        "2026-11-02T10:00:00Z "
        + "\n2026-11-02T10:00:00Z ".join(
            ["aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bC1dE3fG5hI7jK9lM1nO3pQ5rS7tU9vW1xY3zA5b"] * 3
        ),
        "aB3dE5gH",
    ),
    (
        "[10:00:00] "
        + "\n[10:00:01] ".join(
            ["aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bC1dE3fG5hI7jK9lM1nO3pQ5rS7tU9vW1xY3zA5b"] * 3
        ),
        "aB3dE5gH",
    ),
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
    ("SECRET_KEY=django-insecure-abc123xyz", "django-insecure"),
    ("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "wJalrXUtn"),
    ("JWT_SECRET_KEY=topsecretvalue", "topsecretvalue"),
    ("GITHUB_TOKEN_VALUE=abcdef123", "abcdef123"),
    ("X-Api-Key: abcdef123456", "abcdef123456"),
    ("x-api-key=abcdef123456", "abcdef123456"),
    ("DATABASE_PASS=hunter2", "hunter2"),
    ("PGPASSWORD=hunter2", "hunter2"),
    ("pwd=hunter2", "hunter2"),
    ("private_key=abcdef12", "abcdef12"),
    ("access-key=abcdef12", "abcdef12"),
    ("credentials=abcdef12", "abcdef12"),
    ("auth=abcdef12", "abcdef12"),
    ("client-secret: abcdef12", "abcdef12"),
    ('Authorization: Bearer "abc.def.ghi"', "abc.def.ghi"),
    ("AUTHORIZATION: BEARER abc.def.ghi", "abc.def.ghi"),
    ("Authorization:Bearer abc.def.ghi", "abc.def.ghi"),
    ("Proxy-Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
    ("headers={'Authorization': 'Bearer abc.def.ghi'}", "abc.def.ghi"),
    ('"Authorization": "Bearer abc.def.ghi"', "abc.def.ghi"),
    ("GET /login?user=bob&password=hunter2&x=1", "hunter2"),
    ("GET /cb?access_token=abc123def456&state=1", "abc123def456"),
    ("postgres://u:p@ss@db/x", "p@ss"),
    ("mongodb+srv://admin:Sup3r!@cluster0.x.net", "Sup3r"),
    ('"password": "ab<cd"', "ab<cd"),
    ("password=ab<cd", "ab<cd"),
    ("password=ab,cd;ef", "cd;ef"),
    ('"password": "ab\\"cd"', "cd"),
    ("password = 'hunter 2'", "hunter 2"),
    ('password="unterminated hunter2', "hunter2"),
    ("users=admin:$apr1$H6uskkkW$IgXLP6ewTrSuBkTrqE8wj/", "H6uskkkW"),
    ("hash $2y$10$abcdefghijklmnopqrstuv", "abcdefghij"),
    ("hash $6$salt$abcdefghijklmnop", "abcdefghij"),
    ("xoxb-1234567890-abcdefghijkl", "abcdefghijkl"),
    (
        "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk\n-----END OPENSSH PRIVATE KEY-----",
        "b3Blbn",
    ),
    (
        "body\n"
        + "\n".join(["aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bC1dE3fG5hI7jK9lM1nO3pQ5rS7tU9vW1xY3zA5b"] * 3),
        "aB3dE5gH",
    ),
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
    "secret: not set",
    "token: expired",
    "auth failed: invalid credentials: user=bob",
    "credentials: user=bob next=1",
    'level=warn msg="login failed" auth=none',
    "auth=true auth=false auth=ok auth=required",
    "password=*** token=<redacted> secret=${DB_SECRET} key_secret=$SECRET",
    "pass: 5 token=123456 password=null secret=unset",
    "bypass=x compass: north",
    "token_id=abc123 session_id=xyz789",
    "SECRET_KEY_FILE=/run/secrets/key PASSWORD_FILE=/run/secrets/db token_expires_in=3600",
    "Cookie: ",
    "bash -p hunter",
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
    "token_count=5",
    "password_reset=true",
    "token_type=bearer",
    "secret_name=prod-db",
    "keyboard=us",
    "passenger=3",
    "author=bob",
    "cost $5 and $6 each",
    "\n".join(["a" * 64 + "b"] * 3),
    "\n".join(["0123456789abcdef" * 4] * 3),  # three container ids on their own lines
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


import time  # noqa: E402

from bleep.guard.pipeline import guard_text  # noqa: E402

HOSTILE = {
    "dots": "." * 1_000_000,
    "dash_underscore": "-_" * 500_000,
    "dashes": "-" * 1_000_000,
    "a_dot": "a." * 500_000,
    "downloading": "Downloading" + "." * 60_000,
    "base64url": "aB3-_x9Z" * 125_000,
    "eyJ": "eyJ" * 300_000,
    "sk": "sk-" * 300_000,
    "bearer": "bearer " * 150_000,
    "scheme": "a.b+c-" * 100_000,
    "token_colon": "token:" * 160_000,
    "begin": "-----BEGIN PRIVATE KEY-----\n" * 30_000,
    "digits": "1" * 1_000_000,
    "esc": "\x1b[" * 300_000,
    "lt": "<" * 1_000_000,
    "fullwidth_lt": "\uff1c" * 1_000_000,
    "lt_slash": "</" * 500_000,
    "url_rep": "a://b:c" * 150_000,
    "kv": "a=b " * 250_000,
}


@pytest.mark.parametrize("name", list(HOSTILE))
def test_hostile_input_is_fast(name):
    start = time.perf_counter()
    out, _ = guard_text("logs", HOSTILE[name])
    assert time.perf_counter() - start < 1.0
    assert len(out) <= 8300


def test_redact_alone_is_bounded_too():
    start = time.perf_counter()
    redact_with_count("-" * 1_000_000)
    assert time.perf_counter() - start < 1.0


def test_stray_begin_does_not_wipe_the_log():
    text = "-----BEGIN PRIVATE KEY-----\n" + "\n".join(f"line {i} ok" for i in range(300))
    out = redact_text(text)
    assert "line 299 ok" in out and "line 0 ok" in out


def test_secret_in_the_cut_middle_of_a_long_line_is_gone_and_one_in_the_head_is_redacted():
    head = redact_text("a " * 500 + "password=hunter2 " + "y " * 10_000)
    assert "hunter2" not in head and "<redacted>" in head
    middle = redact_text("x " * 5000 + "password=hunter2 " + "y " * 5000)
    assert "hunter2" not in middle and "chars cut" in middle


def test_credentials_pair_not_swallowed():
    out = redact_text("error: invalid credentials: user=bob retries=3")
    assert out == "error: invalid credentials: user=bob retries=3"


def test_fail_safe_values_still_swallow_ampersand_and_comma():
    assert redact_text("password=a&x=1") == "password=<redacted>"
    assert redact_text("password=abc,def") == "password=<redacted>"


def test_cookie_names_survive():
    assert redact_text("Set-Cookie: sid=abc123; Path=/; HttpOnly") == (
        "Set-Cookie: sid=<redacted>; Path=/; HttpOnly"
    )


def test_long_secrets_survive_the_line_cut():
    import re

    jwt = JWT + "x" * 100  # a 200-character secret right at the cut
    ghp = "ghp_" + "A1b2" * 25
    for secret in (jwt, ghp):
        for offset in range(1900, 2100, 10):
            line = "w " * (offset // 2) + secret + " " + "z " * 2500
            out = guard_text("logs", line)[0]
            assert secret[:30] not in out and secret[-30:] not in out, offset
            assert re.search(r"<redacted>|chars cut", out)


def test_event_loop_keeps_ticking_while_guard_runs():
    import asyncio

    async def run():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.001)
                ticks += 1

        task = asyncio.create_task(ticker())
        await asyncio.sleep(0.01)
        before = ticks
        text = "-----BEGIN PRIVATE KEY-----\n" * 200_000
        await asyncio.to_thread(guard_text, "logs", text)
        task.cancel()
        return ticks - before

    assert asyncio.run(run()) >= 5
