import re
import time

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
    "auth failed: invalid credentials: user=bob retries=3",
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


# --- round 3 -------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "reset_password_token=abc123def456",
        "password_reset_token=abc123def456",
        "OAUTH_PROVIDER_CLIENT_SECRET=abc999xyz",
        "STATUS_PAGE_API_KEY=abc999xyz",
        "HEADER_AUTH_TOKEN=abc999xyz",
        "MAX_PASSWORD=hunter2",
        "DIR_PASSWORD=hunter2",
        "MODE_SECRET=x1",
        "id_token=abc999xyz",
        "FILE_ENCRYPTION_KEY=abc999xyz",
        "APP_KEY=base64:abc999xyz",
        "STRIPE_KEY=sk_live_abc123",
        "HMAC_KEY=abc999",
        "AES_KEY=abc999",
        "WEBHOOK_KEY=abc999",
        "PUSHER_APP_KEY=abc999",
        "password=$uperSecret2024",
        "password=$HOME_and_secret",
        'password="${SECRET}actualvalue"',
        "DB_PASSWORD: aB3=xY9!",
        "client_secret: abc=defghi",
        "token: Zm9v=YmFy",
        "credentials: user=bob:hunter2",
        "password => hunter2",
        "password := hunter2",
        "password == hunter2",
        "$cfg = ['password' => 'hunter2']",
        "[password] => hunter2",
        "--db-password hunter2",
        "--token abc999xyz",
        "run --client-secret 'hunter 2' --verbose",
        '{"cookie": "session=abc999; theme=dark"}',
        "request_Cookie=session=abc999",
        "cookie=sid=abc999",
        "password:\n\n\n\n  hunter2",
        "credentials: user=bob pw=hunter2",
    ],
)
def test_round3_secrets(text):
    out = redact_text(text)
    for secret in (
        "hunter2",
        "hunter 2",
        "abc999",
        "Secret2024",
        "actualvalue",
        "aB3",
        "defghi",
        "Zm9v",
    ):
        assert secret not in out or secret in "".join(part for part in ["theme"]), (text, out)
    assert redact_text(out) == out


@pytest.mark.parametrize(
    "text",
    [
        "auth_method=password",
        "auth_mechanism=SCRAM-SHA-256",
        "auth_backend=ldap",
        "mongodb://h/db?authSource=admin&replicaSet=rs0",
        "token_audience=api",
        "secretary=bob",
        "password=''",
        'password=""',
        'password: "not set"',
        "password: 'none'",
        "cache_key=user:42",
        "primary_key=id",
        "sort_key=asc",
        "idempotency_key=abc",
        "--password-stdin",
        "--token-file /run/secrets/t",
        "docker login -p hunter",
        "PWD=/home/app OLDPWD=/root",
        "Cookie: ",
        "missing cookie: please log in",
    ],
)
def test_round3_controls(text):
    assert redact_text(text) == text


def test_flags_only_take_the_next_word():
    assert redact_text("--db-password hunter2 --verbose") == "--db-password <redacted> --verbose"
    assert redact_text("--password --verbose") == "--password --verbose"


def test_cookie_names_survive_in_json():
    assert redact_text('{"cookie": "a=1; b=2"}') == '{"cookie": "a=<redacted>; b=<redacted>"}'


def test_block_scalar_rules():
    text = "db:\n  password: |  # note\n    l1\n\n    l2\n  user: bob\n  host: x\n"
    out = redact_text(text)
    assert "l1" not in out and "l2" not in out
    assert "user: bob" in out and "host: x" in out and out.count("\n") == text.count("\n")
    long = "password: >-\n" + "".join(f"  secret line {i}\n" for i in range(60)) + "next: ok"
    out = redact_text(long)
    assert "secret line" not in out and out.endswith("next: ok")
    assert redact_text(out) == out


def test_one_redaction_per_secret():
    assert redact_with_count("password=ghp_" + "A" * 20 + "!tail")[1] == 1
    assert redact_with_count("Authorization: Bearer abc.def.ghi")[1] == 1
    assert redact_with_count("password: |\n  a\n  b")[1] == 1


def test_secret_straddling_a_line_cut_is_redacted_at_every_offset():
    secret = "hunter2secretvalue"
    for k in range(-20, 21):
        # the boundary between key and value sits k characters from the tail cut ...
        rest = 1850 - k
        tail = "password: " + secret + " " + "y " * ((rest - len(secret)) // 2)
        text_tail = "a " * 2500 + tail
        # ... and k characters from the head cut
        head = "a " * ((1850 + k - 10) // 2)
        text_head = head + "password: " + secret + " " + "y " * 2500
        for text in (text_tail, text_head):
            for out in (guard_text("logs", text)[0], redact_text(text)):
                assert secret not in out, k


def test_cr_chunked_input_is_bounded():
    text = "\r".join("token=abc " * 400 for _ in range(2500))
    start = time.perf_counter()
    guard_text("logs", text)
    assert time.perf_counter() - start < 1.0


@pytest.mark.parametrize(
    "unit",
    [
        "token=abc ",
        "a://:@!",
        "cookie: a=b;",
        "x" * 91 + "token ",
        "<",
        "\uff1c",
        "token: |\n  x\n",
        "-eyJ",
        "ya29.",
        "api_keys: [",
        '<password a="b">',
        'key="password" value=',
        "a,",
    ],
)
@pytest.mark.parametrize("length", [4000, 4001])
def test_maximal_tool_input_is_fast(unit, length):
    # 4,001 characters is the shortest line that is cut, where both windows cover most of it
    line = (unit * (length // len(unit) + 1))[:length]
    text = "\n".join([line] * 1000)
    start = time.perf_counter()
    out, _ = guard_text("logs", text)
    assert time.perf_counter() - start < 1.0
    assert len(out) <= 8300


@pytest.mark.parametrize("unit", ["<", "a://:@!", "-eyJ", "token=abc,"])
@pytest.mark.parametrize("mark", [" ", "["])
def test_a_tail_window_reaching_far_back_is_fast(unit, mark):
    # one space or `[` near the start lets the tail window of a 4,001-character line start there
    line = (unit * 4001)[:4001]
    line = line[:160] + mark + line[161:]
    start = time.perf_counter()
    out, _ = guard_text("logs", "\n".join([line] * 1000))
    assert time.perf_counter() - start < 1.0
    assert len(out) <= 8300


@pytest.mark.parametrize(("unit", "length"), [("token=abc ", 4000), ("-eyJ", 4001)])
def test_event_loop_gap_stays_small_on_the_worst_input(unit, length):
    import asyncio

    line = (unit * length)[:length]
    text = "\n".join([line] * 1000)

    async def run():
        gaps, last, done = [], time.perf_counter(), False

        async def ticker():
            nonlocal last
            while not done:
                await asyncio.sleep(0.001)
                now = time.perf_counter()
                gaps.append(now - last)
                last = now

        task = asyncio.create_task(ticker())
        await asyncio.sleep(0.01)
        await asyncio.to_thread(guard_text, "logs", text)
        done = True
        await task
        return max(gaps)

    assert asyncio.run(run()) < 0.3


def test_cut_note_counts_original_characters():
    text = "x\n" * 300_000 + "tail"  # 600,004 characters; 64 x 8000 = 512,000 are kept
    out = guard_text("logs", text)[0]
    note = re.search(r"\[… (\d+) chars cut\]", out)
    assert note and int(note.group(1)) >= len(text) - 512_000


def _set_env(key: str, value: str):
    from bleep.guard.redact import redacted_step
    from bleep.schemas.graph import PlanStep

    step = PlanStep(
        action="coolify.set_env", host_id="a", target="shop", params={"key": key, "value": value}
    )
    return redacted_step(step).params["value"]


@pytest.mark.parametrize(
    "value",
    ["correct horse battery staple", "line1\nline2secret", '"a b" tail', "sk_live_x y"],
)
def test_a_set_env_value_under_a_secret_key_is_redacted_whole(value):
    for key in ("PASSWORD", "STRIPE_KEY", "API_TOKEN"):
        assert _set_env(key, value) == "<redacted>"


def test_a_set_env_value_under_a_plain_key_keeps_all_but_its_secrets():
    shown = _set_env("DATABASE_URL", "postgres://shop:pw-fake-123@attacker.example/shop")
    assert shown == "postgres://shop:<redacted>@attacker.example/shop"
    assert _set_env("PORT", "3000") == "3000"


@pytest.mark.parametrize(
    "key",
    [
        "AUTH_HOST",
        "AUTH_DOMAIN",
        "AUTH0_DOMAIN",
        "AUTH_SERVER",
        "AUTH_PROXY",
        "AUTH_SERVICE_HOST",
        "TOKEN_ISSUER",
        "SECRET_HOST",
        "DB_PASS_HOST",
        "authCallbackUrl",
    ],
)
def test_a_secret_word_next_to_a_place_word_shows_the_value(key):
    assert _set_env(key, "attacker.example") == "attacker.example"


def test_a_shown_place_still_has_its_password_redacted():
    assert _set_env("SECRET_HOST", "postgres://u:pw-fake-123@attacker.example") == (
        "postgres://u:<redacted>@attacker.example"
    )


SECRET_NAMED = [
    "KEY", "STRIPE_SK", "SALT", "SIGNING_SALT", "JWT", "HMAC", "GITHUB_PAT", "PGPASS",
]  # fmt: skip


@pytest.mark.parametrize("key", SECRET_NAMED)
def test_more_secret_key_names_are_recognised(key):
    from bleep.guard.redact import _is_secret_key

    assert _is_secret_key(key)
    assert _set_env(key, "s3cr3t-value-xyz") == "<redacted>"
    assert redact_text(f"{key}=s3cr3t-value-xyz ok") == f"{key}=<redacted> ok"


@pytest.mark.parametrize(
    "key",
    ["SENTRY_HOST", "JWT_ISSUER", "HMAC_ENDPOINT", "SALT_SERVER", "GITHUB_PAT_URL", "KEY_DOMAIN"],
)
def test_a_place_named_after_a_new_secret_word_stays_visible(key):
    assert _set_env(key, "attacker.example") == "attacker.example"


@pytest.mark.parametrize(
    "text",
    [
        "cache miss key=user:42",
        "path=/var/lib/app pattern=*.log",
        "task=backup disk=sda1",
        "PGPASSFILE=/root/.pgpass",
        "SALT_ROUNDS=12 JWT_ALGORITHM=HS256 JWT_EXPIRES_IN=1h",
    ],
)
def test_new_secret_words_leave_plain_settings_alone(text):
    assert redact_text(text) == text


DSNS = [
    ("https://abc123@o0.ingest.sentry.io/1", "https://<redacted>@o0.ingest.sentry.io/1"),
    ("postgres://u:pw-fake-123@db:5432/x", "postgres://u:<redacted>@db:5432/x"),
    ("https://attacker.example/1", "https://attacker.example/1"),
]


@pytest.mark.parametrize(("value", "shown"), DSNS)
@pytest.mark.parametrize("key", ["SENTRY_DSN", "DATABASE_DSN", "sentryDsn", "DSN"])
def test_a_dsn_is_a_place_whose_userinfo_is_hidden_in_an_approval(key, value, shown):
    assert _set_env(key, value) == shown


@pytest.mark.parametrize(("value", "shown"), DSNS)
@pytest.mark.parametrize("template", ["SENTRY_DSN={} ok", 'sentry_sdk.init(dsn="{}")', "dsn: {}"])
def test_a_dsn_in_free_text_keeps_its_host_and_hides_its_userinfo(template, value, shown):
    out = redact_text(template.format(value))
    assert out == template.format(shown)
    assert redact_text(out) == out


def test_a_bare_user_outside_a_dsn_stays_readable():
    text = "clone ssh://git@github.com/acme/shop.git and APP_URL=https://bob@shop.example/"
    assert redact_text(text) == text


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("DATABASE_DSN=postgres://h/x?password=hunter2", "hunter2"),
        ("dsn=postgres://h/x?password=hunter2", "hunter2"),
        ("SENTRY_DSN=https://h/1?sentry_key=abcdef123456&sentry_secret=zzz", "abcdef123456"),
        ("JWT_ISSUER=https://idp/?client_secret=abc123", "abc123"),
        ("AUTH_CALLBACK=https://app/cb?token=abcdef123456", "abcdef123456"),
        ("TOKEN_REDIRECT=https://app/cb?access_token=abc123456", "abc123456"),
        ("AUTH_PROXY=http://p:3128?password=x1", "x1"),
        ("PASSWORD_HOST=hunter2", "hunter2"),
        ("DB_PASSWORD_SERVER=hunter2", "hunter2"),
        ("SECRET_DSN=s3cr3t", "s3cr3t"),
        ("PASSWORD_DSN=hunter2", "hunter2"),
        ("TOKEN_URL=https://api.x/cb?token=abcdef123456", "abcdef123456"),
        ("A_URL=https://x/?B_URL=https://y/?token=deep123456", "deep123456"),
        ("SENTRY_DSN=https://k:@h/1", "k:"),
    ],
)
def test_a_secret_inside_a_kept_value_or_under_a_place_after_a_secret_word_is_redacted(
    text, secret
):
    out = redact_text(text)
    assert secret not in out
    assert redact_text(out) == out


def test_pairs_nested_without_end_stay_bounded_and_redacted():
    text = "path=" * 800 + "token=abcdef123456"
    started = time.perf_counter()
    out = redact_text(text)
    assert time.perf_counter() - started < 1
    assert "abcdef123456" not in out


@pytest.mark.parametrize(
    ("key", "value"),
    [("AUTH0_DOMAIN", "evil.auth0.com"), ("JWT_ISSUER_URL", "https://evil.example/")],
)
def test_an_approval_still_shows_a_place_after_a_secret_word(key, value):
    assert _set_env(key, value) == value


@pytest.mark.parametrize("key", ["SENTRY_DSN_SECRET", "DSN_PASSWORD"])
def test_dsn_is_a_place_only_as_the_last_word_of_a_key(key):
    assert _set_env(key, "s3cr3t-value") == "<redacted>"


@pytest.mark.parametrize(
    ("text", "count"),
    [
        ("SENTRY_DSN=https://k@h/1?auth=tok123456", 2),
        ('TOKEN_URL="token=abc123456 secret=def123456"', 2),
        ("Cookie: a=1x; b=2y", 2),
        ("password=a token=b", 2),
    ],
)
def test_the_count_is_one_per_secret_value_inside_a_kept_value_too(text, count):
    out, found = redact_with_count(text)
    assert found == count == out.count("<redacted>")
    assert redact_with_count(out) == (out, 0)
