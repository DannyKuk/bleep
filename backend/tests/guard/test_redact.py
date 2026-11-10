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


def _best_of_three(text: str) -> float:
    times = []
    for _ in range(3):
        start = time.perf_counter()
        guard_text("logs", text)
        times.append(time.perf_counter() - start)
    return min(times)


def assert_no_slower_than_benign(text: str) -> None:
    # Relative to benign text of the same shape and size, so a slow CI runner scales both sides;
    # a quadratic regression is hundreds of times slower, the worst input here about 2 times.
    benign = "\n".join(["hello world " * (len(line) // 12 + 1) for line in text.split("\n")])
    assert _best_of_three(text) < 10 * _best_of_three(benign)


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
        "token: [\n  a,\n",
        "<password><![CDATA[",
        '<entry key="password">',
        'value="a" key="password" ',
        '"cookie": "a=b; ',
        "token: [\n  a\n]x,",
        "token: [\n  a:\n",
    ],
)
@pytest.mark.parametrize("length", [4000, 4001])
def test_maximal_tool_input_is_fast(unit, length):
    # 4,001 characters is the shortest line that is cut, where both windows cover most of it
    line = (unit * (length // len(unit) + 1))[:length]
    text = "\n".join([line] * 1000)
    assert_no_slower_than_benign(text)
    out, _ = guard_text("logs", text)
    assert len(out) <= 8300


@pytest.mark.parametrize("unit", ["<", "a://:@!", "-eyJ", "token=abc,"])
@pytest.mark.parametrize("mark", [" ", "["])
def test_a_tail_window_reaching_far_back_is_fast(unit, mark):
    # one space or `[` near the start lets the tail window of a 4,001-character line start there
    line = (unit * 4001)[:4001]
    line = line[:160] + mark + line[161:]
    text = "\n".join([line] * 1000)
    assert_no_slower_than_benign(text)
    out, _ = guard_text("logs", text)
    assert len(out) <= 8300


@pytest.mark.parametrize(("unit", "length"), [("token=abc ", 4000), ("-eyJ", 4001)])
def test_event_loop_gap_stays_small_on_the_worst_input(unit, length):
    line = (unit * length)[:length]
    assert _event_loop_gap("\n".join([line] * 1000)) < 0.3


@pytest.mark.parametrize(
    ("opener", "unit"),
    [
        ("API_KEYS=", "a,"),
        ("API_KEYS=", "-"),
        ('"api_keys": "', "a "),
        ('"api_keys": "', "\\"),
        ("Cookie: ", "-"),
        ("Cookie: ", "a=b; "),
        ("", "cookie"),
        ("", 'cookie="a" '),
        ("", "pw=a "),
        ("url=x?", "path="),
    ],
)
def test_a_value_opened_far_back_on_a_long_line_is_fast(opener, unit):
    # the tail window reads the whole line once for the value open where it starts
    line = (opener + unit * 512_000)[:512_000]
    assert_no_slower_than_benign(line)
    assert _event_loop_gap(line) < 0.3


def _event_loop_gap(text: str) -> float:
    import asyncio

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

    return asyncio.run(run())


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


def test_the_jwt_patterns_match_what_the_plain_regexes_matched():
    """`_JWT` and `_hide_cut_jwt` read a run once; on any input they must redact exactly what the
    plain (quadratic) regexes did, including short `eyJ` runs right before a real token."""
    import random

    from bleep.guard.redact import _JWT, _hide_cut_jwt, _redact_jwt

    start = r"(?:(?<!\w)|(?<=%[0-9A-Fa-f]{2}))"
    plain = re.compile(start + r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")
    plain_cut = re.compile(start + r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*\Z")
    parts = ["eyJ", ".", "-", "_", "a", "ab", "abcde", "x9Z", " ", "é", "eyJab.", JWT, "%3D", "%"]
    rng = random.Random(58)
    for _ in range(50_000):
        text = "".join(rng.choice(parts) for _ in range(rng.randrange(1, 14)))
        assert _JWT.sub(_redact_jwt, text) == plain.sub("<redacted>", text), text
        assert _hide_cut_jwt(text) == plain_cut.sub("<redacted>", text), text


# Vendor token shapes (#388). Each is built from parts, so no complete token sits in the source.
B24 = "4eC39HqLyjWDarjtT1zdp7dc"
B36 = "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
B35 = "SyD-9tSrke72PouQMnMX-a7eZSW0jkFMBWY"
B32 = "RUe7IZgC6384GrI1x9QpL2mN4vB8kT0z"
TOKEN_SHAPES = {
    "stripe_secret": (
        "charge failed with sk_" + "live_" + B24,
        "sk_" + "live_" + B24,
        "pk_live_" + B24 + " sk_live_mode",
    ),
    "stripe_restricted": (
        "client=rk_" + "live_" + B24 + "&retry=1",
        "rk_" + "live_" + B24,
        "rk_live_" + B24[:12] + " disk_live_" + B24,
    ),
    "stripe_sandbox": ("key sk_" + "test_" + B24, "sk_" + "test_" + B24, "sk_test_suite passed"),
    "gitlab": (
        "remote: glpat-" + B24[:20] + " expired",
        "glpat-" + B24[:20],
        "see glpat-tokens in the docs, glpat-" + B24[:19],
    ),
    "gitlab_routable": (
        "token glpat-" + "YzowCm86MQp1OjFSDdqkLmVS337oi4qlKHnlEA" + ".01.0r1ajj2i3 in use",
        "0r1ajj2i3",
        "glpat-" + "YzowCm86MQp1" + ".01.0r1ajj2i3",
    ),
    "gitlab_routable_one_dot": (
        "token glpat-" + "YzowCm86MQp1OjFSDdqkLmVS337oi4qlKHnlEA" + ".121e8269o in use",
        "121e8269o",
        "glpat-" + "YzowCm86MQp1" + ".121e8269o",
    ),
    "npm": (
        "npm notice publishing with npm_" + B36 + " as ci",
        "npm_" + B36,
        "npm_config_registry=https://registry.npmjs.org/ npm_" + B36[:35],
    ),
    "google_api_key": (
        "GET https://maps.googleapis.com/maps/api/js?key=AIza" + B35 + "&callback=init",
        "AIza" + B35,
        "AIzawesome AIza" + B35[:34] + " AIza" + B35 + "X",
    ),
    "pushbullet": (
        "pushbullet push failed for o." + B32 + ": 401",
        "o." + B32,
        "cdn.o.example.com o.d41d8cd98f00b204e9800998ecf8427e x.o." + B32 + " o." + B32 + ".com",
    ),
    "slack_legacy": (
        "slack: xoxo-" + "2493846294-9283746592-3826372821-0f3a9c2e1d",
        "9283746592",
        "xoxo-best-wishes-from-everyone",
    ),
    "slack_refresh": (
        "refresh_with=xoxe-1-" + "My0xLTIxMjM0NTY3ODkwLTE",
        "My0xLTIxMjM0",
        "xoxe-reply-to-all-of-us",
    ),
    "slack_app": (
        "SLACK_APP=xapp-1-" + "A0123ABCDEF-1234567890123-abc123def456",
        "A0123ABCDEF",
        "xapp-config-loaded",
    ),
}

# More shapes (#416): GitLab's other prefixes in the lengths GitLab's own secret detection rules
# give, Google `AQ.` keys, Stripe organization keys and Slack's browser tokens.
HEX40 = "0f3a9c2e1d" * 4
B64 = B32 * 2
B50 = B36 + B24[:14]
B43 = B36 + "aB3dE5f"
ROUTABLE = "YzowCm86MQp1OjFSDdqkLmVS337oi4qlKHnlEA" + ".01.0r1ajj2i3"
TOKEN_SHAPES |= {
    "gitlab_runner": (
        "runner token glrt-" + B24[:20] + " registered",
        "glrt-" + B24[:20],
        "glrt-" + B24[:19] + " glrt-" + B24 + " glrt-runner-registration",
    ),
    "gitlab_runner_partition": ("token glrt-t1_" + B24[:20], B24[:20], "glrt-t4_" + B24[:20]),
    "gitlab_runner_registered": (
        "glrtr-" + B24[:20] + " ok",
        "glrtr-" + B24[:20],
        "glrtr-" + B24[:18],
    ),
    "gitlab_runner_routable": (
        "token glrt-" + ROUTABLE + " in use",
        "0r1ajj2i3",
        "glrt-x.01.0r1ajj2i3",
    ),
    "gitlab_runner_controller": ("glrct-" + B24[:20], "glrct-" + B24[:20], "glrct-" + B24[:21]),
    "gitlab_runner_registration": (
        "register with GR1348941" + B24[:20],
        B24[:20],
        "GR1348941" + B24[:10],
    ),
    "gitlab_deploy": ("clone with gldt-" + B24[:20], "gldt-" + B24[:20], "gldt-" + B24[:16]),
    "gitlab_deploy_routable": (
        "gldt-" + ROUTABLE,
        "0r1ajj2i3",
        "gldt-" + B24[:12] + ".01.0r1ajj2i3",
    ),
    "gitlab_ci_job": ("CI_JOB_TOKEN glcbt-64_" + B24[:20], B24[:20], "glcbt-64_" + B24[:19]),
    "gitlab_trigger": ("trigger glptt-" + HEX40 + " sent", HEX40, "glptt-" + HEX40[:30]),
    "gitlab_trigger_short": ("trigger glptt-" + B24[:20] + " sent", B24[:20], "glptt-" + B24[:21]),
    "langsmith_personal": (
        "LangSmith: using lsv2_" + "pt_" + HEX40[:32] + "_" + HEX40[:10] + " for traces",
        HEX40[:32],
        "lsv2_pt_" + HEX40[:31] + "_" + HEX40[:10] + " lsv2_pt_settings",
    ),
    "langsmith_service": (
        "key lsv2_" + "sk_" + HEX40[8:40] + "_" + HEX40[:10],
        HEX40[8:40],
        "lsv2_sk_" + HEX40[8:40] + "_" + HEX40[:11],
    ),
    "gitlab_feed": ("feed glft-" + B24[:20], "glft-" + B24[:20], "glft-feed-token-docs-v2"),
    "gitlab_feed_path": ("glft-" + HEX40 + HEX40[:24] + "-42", HEX40, "glft-" + HEX40[:24] + "-42"),
    "gitlab_incoming_mail": (
        "incoming+glimt-" + "a1b2c3d4e5f6g7h8i9j0k1l2m" + "@example.com",
        "a1b2c3d4e5f6g7h8i9j0",
        "glimt-" + "a1b2c3d4e5f6",
    ),
    "gitlab_agent": (
        "agent token glagent-" + B50,
        B50,
        "glagent-configuration-repository-for-the-cluster",
    ),
    "gitlab_agent_routable": ("glagent-" + ROUTABLE, "0r1ajj2i3", "glagent-" + B24 + ".01"),
    "gitlab_workspace": ("glwt-" + ROUTABLE + " ok", "0r1ajj2i3", "glwt-" + B24[:20]),
    "gitlab_scim": ("glsoat-" + B24[:20], "glsoat-" + B24[:20], "glsoat-" + B24[:12]),
    "gitlab_feature_flags": ("glffct-" + B24[:20], "glffct-" + B24[:20], "glffct-client"),
    "gitlab_oauth_secret": ("secret gloas-" + B64, B64, "gloas-" + B32),
    "gitlab_iam_refresh": ("gliamrt-" + B43 + "." + B43, B43, "gliamrt-" + B43),
    "gitlab_iam_code": ("code=gliamac-" + B43 + "." + B43, B43, "gliamac-" + B32 + "." + B32),
    "google_auth_key": (
        "gemini call with AQ." + B36 + B24[:14],
        "AQ." + B36,
        "see FAQ." + B36 + B24[:14] + " AQ." + B24,
    ),
    "stripe_organization": (
        "org key sk_" + "org_" + B24 + " used",
        "sk_" + "org_" + B24,
        "sk_org_settings sk_org_live_mode",
    ),
    "stripe_organization_live": (
        "sk_" + "org_live_" + B24,
        "sk_" + "org_live_" + B24,
        "rk_org_live_" + B24,
    ),
    "slack_client": (
        "slack client token xoxc-" + "2493846294-9283746592-3826372821-" + HEX40,
        "9283746592",
        "xoxc-short",
    ),
    "slack_cookie": (
        "d=xoxd-" + B36 + "%2F" + B24 + "%3D%3D; path=/",
        B36,
        "xoxd-" + "abc%2F",
    ),
}


@pytest.mark.parametrize("shape", list(TOKEN_SHAPES))
def test_a_vendor_token_is_redacted_wherever_it_appears(shape):
    text, secret, _ = TOKEN_SHAPES[shape]

    out, count = redact_with_count(text)

    assert secret not in out and "<redacted>" in out
    assert count == 1
    assert secret not in guard_text("logs", text)[0]


@pytest.mark.parametrize("shape", list(TOKEN_SHAPES))
def test_a_near_miss_of_a_vendor_token_stays(shape):
    _, _, near_miss = TOKEN_SHAPES[shape]

    assert redact_with_count(near_miss) == (near_miss, 0)


ON_SIGHT = [
    "ghp_" + B36,
    "github_pat_" + B36,
    "sk-" + B24,
    "sk-or-" + B24,
    "xoxb-" + "2493846294-" + B24,
    "xoxc-" + "2493846294-" + B24,
    "xoxd-" + B36,
    "xoxe-1-" + B24,
    "xapp-1-" + B24,
    JWT,
    "ya29." + B36,
    "AIza" + B35,
    "AQ." + B36 + B24[:14],
    "sk_" + "live_" + B24,
    "sk_" + "org_" + B24,
    "glpat-" + B24[:20],
    "glrt-" + B24[:20],
    "gldt-" + ROUTABLE,
    "GR1348941" + B24[:20],
    "lsv2_" + "sk_" + HEX40[:32] + "_" + HEX40[:10],
    "gliamrt-" + B43 + "." + B43,
    "npm_" + B36,
    "o." + B32,
    "AKIA" + "IOSFODNN7EXAMPLE",
    TELEGRAM,
    "pbkdf2_sha256$600000$" + B24,
]


@pytest.mark.parametrize("token", ON_SIGHT)
@pytest.mark.parametrize("escape", ["%3D", "%3d", "%20", "%3A"])
def test_a_token_after_a_percent_encoded_byte_is_redacted(token, escape):
    text = f"GET /login?next=%2Fcb%3Fk{escape}{token}&state=1 302"

    out = redact_text(text)

    assert token not in out and f"{escape}<redacted>" in out


def test_the_literal_first_patterns_match_what_the_start_first_ones_did():
    """Each token prefix is written before its start check (`_starting`) so `re` can scan for it;
    the same module with every prefix written after its start must find the same spans."""
    import inspect
    import random
    import types

    from bleep.guard import redact

    source = inspect.getsource(redact)
    written = 'return rf"{prefix}(?<={start}{prefix})"'
    assert written in source
    module = types.ModuleType("start_first")
    exec(
        compile(source.replace(written, 'return rf"{start}{prefix}"'), "start_first", "exec"),
        module.__dict__,
    )
    names = ["_JWT", "_JWT_START", "_GITLAB"]
    pairs = [(getattr(redact, n), getattr(module, n)) for n in names]
    pairs += [(a, b) for (a, _), (b, _) in zip(redact._PATTERNS, module._PATTERNS, strict=True)]
    assert all(a.pattern != b.pattern for a, b in pairs[:2])
    prefixes = ["ghp_", "github_pat_", "sk-", "sk-or-", "xoxb-", "xoxd-", "xoxe-1-", "xapp-1-"]
    prefixes += ["eyJ", "ya29.", "AIza", "AQ.", "sk_live_", "rk_test_", "sk_org_", "sk_org_live_"]
    prefixes += ["lsv2_pt_", "glpat-", "glrt-", "glptt-", "gliamrt-", "GR1348941", "npm_", "o."]
    prefixes += ["AKIA", "ASIA", "123456789:", "pbkdf2_sha256$1$"]
    fillers = ["%3D", "%", "%2", "x", "_", "-", ".", " ", "é", "٣", "0", "\n", B24, HEX40, JWT]
    rng = random.Random(416)
    for _ in range(4000):
        text = "".join(rng.choice(prefixes + fillers) for _ in range(rng.randrange(1, 10)))
        for new, old in pairs:
            assert [m.span() for m in new.finditer(text)] == [
                m.span() for m in old.finditer(text)
            ], (new.pattern, text)


def test_a_vendor_token_at_the_end_of_a_sentence_is_redacted():
    for token in ("o." + B32, "AIza" + B35, "npm_" + B36, "glpat-" + B24[:20]):
        assert redact_text(f"the key was {token}.") == "the key was <redacted>."


def test_a_vendor_token_under_a_secret_key_is_one_redaction():
    assert redact_with_count("STRIPE_SECRET_KEY=sk_" + "live_" + B24) == (
        "STRIPE_SECRET_KEY=<redacted>",
        1,
    )


@pytest.mark.parametrize(
    "unit",
    [
        "o.Aa",
        "o.",
        "AIza",
        "sk_live_",
        "glpat-",
        "npm_a",
        "xoxo-1-",
        "xapp-1",
        "glrt-a",
        "glcbt-1_",
        "gliamrt-a.",
        "GR1348941",
        "AQ.a",
        "sk_org_",
        "xoxd-%",
        "%3D",
        "%3Dghp_",
        "glrt-",
        "glagent-",
        "xghp_",
        "AIzaAIza",
        "token: | x ",
        "password: |  ",
        "xhttps://a:b",
    ],
)
def test_lines_of_vendor_token_prefixes_are_fast(unit):
    line = (unit * 4001)[:4001]
    text = "\n".join([line] * 1000)
    assert_no_slower_than_benign(text)
    out, _ = guard_text("logs", text)
    assert len(out) <= 8300


# A YAML block header with its value on the same line (#417): VT, FF and NEL are line breaks
# now, so they no longer join the two, and text written so is handled too.
@pytest.mark.parametrize("separator", ["\x85", "\x0b", "\x0c"])
def test_a_block_value_after_a_vt_ff_or_nel_is_redacted(separator):
    out, _ = guard_text("logs", f"password: |{separator}  hunter2hunter2")

    assert "hunter2hunter2" not in out and "password: |" in out


@pytest.mark.parametrize("separator", ["\x85", "\x0b", "\x0c"])
def test_vt_ff_and_nel_become_line_breaks(separator):
    from bleep.guard.sanitize import strip_control

    assert strip_control(f"a{separator}b\x07c") == "a\nbc"


def test_a_secret_glued_by_a_vt_no_longer_reads_as_part_of_the_word_before_it():
    out, _ = guard_text("logs", "export\x0bsk_" + "live_" + B24)

    assert B24 not in out


@pytest.mark.parametrize("separator", ["\n", "\x85"])
def test_a_joined_line_ending_in_another_header_still_hides_the_value_and_its_block(separator):
    text = f"password: |  hunter2hunter2 token: |{separator}  abc123secret\nhost: db"

    out, _ = guard_text("logs", text)

    assert "hunter2hunter2" not in out and "abc123secret" not in out and "host: db" in out


def test_a_header_digit_run_is_a_value_not_an_indentation_indicator():
    assert redact_text("password: |4242") == "password: <redacted>"
    assert redact_text("password: |2-\n    hunter2") == "password: |2-\n    <redacted>"


@pytest.mark.parametrize("separator", ["\x0b", "\x0c", "\x85", ""])
def test_a_value_right_after_a_header_at_the_keys_indentation_is_redacted(separator):
    out, _ = guard_text("logs", f"password: |{separator}4242")

    assert "4242" not in out


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("password: |\x0c\x0c4242", "4242"),
        ("password: |\n---Pa55", "---Pa55"),
        ("password: |\n...Pa55", "...Pa55"),
    ],
)
def test_a_value_past_one_blank_line_or_glued_to_a_document_marker_is_redacted(text, secret):
    out, _ = guard_text("logs", text)

    assert secret not in out


@pytest.mark.parametrize(
    "text",
    [
        "password: |\nuser: bob",
        "password: |\n- item",
        "password: |\n# a comment",
        "password: |\n---\nuser: bob",
        "api_key: >\n2026-11-02T10:00:00Z INFO next record",
        "detoxoxc-something-very-long-name",
        "Usage: set password: |\nthen type it below",
        "secret: >\nINFO server started on :8080",
        "echo secret: >\nserver started on :8080",
    ],
)
def test_a_line_after_a_header_that_is_structure_or_a_new_record_stays(text):
    assert redact_with_count(text) == (text, 0)


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        (
            "Error: auth: > 3 failures\n  at Login.check (login.js:12)\n  at main (app.js:3)",
            "Error: auth: > <redacted>\n  at Login.check (login.js:12)\n  at main (app.js:3)",
        ),
        (
            "$ echo token: > /tmp/out\n  done in 3s",
            "$ echo token: > <redacted>\n  done in 3s",
        ),
        (
            "| Token: | Description |\n  | api | the API's key |",
            "| Token: | <redacted>\n  | api | the API's key |",
        ),
    ],
)
def test_a_header_after_other_text_hides_its_value_but_no_lines_below(text, shown):
    assert redact_text(text) == shown


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        (
            "- password: |  hunter2hunter2\n  user: bob\n- name: x",
            "- password: |  <redacted>\n  user: bob\n- name: x",
        ),
        (
            "- password: |\n    hunter2hunter2\n  user: bob",
            "- password: |\n    <redacted>\n  user: bob",
        ),
    ],
)
def test_a_list_items_block_ends_at_its_sibling_keys(text, shown):
    assert redact_text(text) == shown


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("password: |  hunter2hunter2", "password: |  <redacted>"),
        ("password: >-  correct horse battery", "password: >-  <redacted>"),
        ('"api_key": |2 aB3dE5fG7h', '"api_key": |2 <redacted>'),
        (
            "db:\n  password: |  line1\n    line2\n  host: db",
            "db:\n  password: |  <redacted>\n    <redacted>\n  host: db",
        ),
        ("name: |  token: |  s3cr3tvalue", "name: |  token: |  <redacted>"),
    ],
)
def test_a_block_header_with_its_value_on_the_same_line_hides_the_value(text, shown):
    out, count = redact_with_count(text)

    assert out == shown
    assert count == shown.count("<redacted>")
    assert redact_with_count(out) == (out, 0)


@pytest.mark.parametrize(
    "text",
    [
        "password: |",
        "password: | # set below",
        "description: |  hello world",
        "password: |  not set",
        "token: |  <redacted>",
    ],
)
def test_a_block_header_without_a_secret_on_its_line_stays(text):
    assert redact_with_count(text) == (text, 0)


GLUED = [
    ("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxhttps://admin:pw@db.example.com", "pw@"),
    ("default:-postgresql://shop:shop@shop-db/shop", "shop@shop-db"),
    ("1https://admin:pw@db.example.com", "pw@"),
    ("tokenghp_" + B36, B36),
    ("x_ghp_" + B36, B36),
    ("idxgithub_pat_" + B36, B36),
    ("idxoxb-" + "2493846294-" + B24, B24),
    ("cookie_dxoxd-" + B36, B36),
    ("keyAIza" + B35, B35),
    ("ciglpat-" + B24[:20], B24[:20]),
    ("runner_glrt-" + B24[:20], B24[:20]),
    ("hashpbkdf2_sha256$600000$" + B24, B24),
]


@pytest.mark.parametrize(
    ("text", "secrets"),
    [
        ("glrt-" + B24[:20] + ".glrt-" + B32[:20], [B24[:20], B32[:20]]),
        ("x-gldt-foo-glrt-" + B24[:20], [B24[:20]]),
        (
            "id=glimt-" + "a1b2c3d4e5f6g7h8i9j0k1l" + "-glrt-" + B24[:20],
            ["a1b2c3d4e5f6g7h8i9j0k1l", B24[:20]],
        ),
    ],
)
def test_every_gitlab_token_in_one_run_is_redacted(text, secrets):
    out = redact_text(text)

    assert not any(secret in out for secret in secrets), out


@pytest.mark.parametrize("word", ["laughs_", "highs_", "x_gho_"])
def test_a_word_ending_in_a_github_prefix_keeps_what_follows_unless_it_is_a_full_token(word):
    name = word + "AndCriesAtTheEndOfTheParty"

    assert redact_text(name) == name


@pytest.mark.parametrize(("text", "secret"), GLUED)
def test_a_secret_glued_to_a_preceding_word_is_redacted(text, secret):
    out, count = redact_with_count(text)

    assert secret not in out and count == 1


@pytest.mark.parametrize(
    "text",
    [
        "disk-utilization-monitor-for-node",
        "disk_live_" + B24,
        "heyJohnny.hello.world",
        "see FAQ." + B36 + B24[:14],
        "EURASIA" + "FOUNDATION2024XYZW",
        "maya29.example.internal.zone.local",
        "foxapp-1-release-build-for-staging",
        "pnpm_" + B36,
    ],
)
def test_a_token_pattern_left_anchored_keeps_a_glued_look_alike(text):
    assert redact_with_count(text) == (text, 0)
