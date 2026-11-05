import json
import re
from pathlib import Path

import pytest

from bleep.guard.pipeline import guard_text
from bleep.guard.redact import WINDOW, redact_text, redact_with_count
from bleep.guard.sanitize import MAX_LINE, cut_points

CORPUS = json.loads((Path(__file__).parent / "fixtures" / "redaction_corpus.json").read_text())
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
HALF = (MAX_LINE - 300) // 2


def test_corpus_secrets_are_all_redacted():
    leaks = [
        text
        for text, secret in CORPUS["secrets"]
        if secret in redact_text(text) or secret in guard_text("logs", text)[0]
    ]
    assert len(CORPUS["secrets"]) > 1000
    assert leaks == []


def test_corpus_controls_stay_readable():
    assert [c for c in CORPUS["controls"] if redact_text(c) != c] == []


@pytest.mark.parametrize(
    ("secret", "needles"),
    [
        ("Authorization: Bearer hUnt3rZq9secretvalueABCDEFGH", ["hUnt3rZq9", "valueABCDEFGH"]),
        ("key sk-proj-" + "Q" * 30 + "Zx9" + "W" * 60, ["W" * 12, "Q" * 12]),
        ("jwt " + JWT, ["SflKxwRJSMeK", "V_adQssw5c", "eyJzdWIiOiIx"]),
    ],
)
def test_secret_across_the_far_window_edge_is_redacted(secret, needles):
    # The secret starts 40 characters before the old window edge (tail + 300) and ends 40 after.
    covered = set()
    for after in range(1450 - len(secret), 1650):
        for pad in ("a ", " a"):  # both phases, so the tail cut lands on every offset
            line = pad * 2500 + " " + secret + " " + ("y " * 1200)[:after]
            tail = cut_points(line, HALF, at_value_ends=True)[1]
            covered.add((tail + WINDOW) - line.index(secret))
            for out in (redact_text(line), guard_text("logs", line)[0]):
                assert not any(n in out for n in needles), after
    assert set(range(-40, len(secret) + 41)) <= covered


def test_long_token_on_the_cut_is_dropped_whole():
    token = "sk-proj-" + "Q" * 300 + "W" * 300
    for offset in range(-500, 100, 25):
        line = "a " * ((len("a " * 2500) + offset) // 2) + token + " " + "y " * 900
        out = redact_text(line)
        assert "Q" * 8 not in out and "W" * 8 not in out, offset


def test_a_line_without_whitespace_keeps_head_and_tail():
    out = redact_text("a" * 10_000 + "TAILEND")
    assert out.startswith("aaa") and out.endswith("TAILEND")


@pytest.mark.parametrize(
    "text",
    [
        "password=${DB_PASSWORD:-hUnt3rZq9}",
        "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-hUnt3rZq9}",
        "password=${DB_PASSWORD:=hUnt3rZq9}",
        "password=${DB_PASSWORD-hUnt3rZq9}",
        "auth: token=hUnt3rZq9 user=bob",
        "credentials: password=hUnt3rZq9 user=bob",
        "auth: user=bob pass=hUnt3rZq9",
        "auth: user=bob,token=hUnt3rZq9 retries=3",
        "PASSWORDS=hUnt3rZq9",
        "DB_PASSWORDS=hUnt3rZq9",
        "API_KEYS=hUnt3rZq9",
        "api_keys: hUnt3rZq9",
        "cookies: sid=hUnt3rZq9",
        "2026-11-02T10:00:00Z password: |\n  hUnt3rZq9",
        "config: password: |\n  hUnt3rZq9",
        "web-1  | password: |\nweb-1  |   hUnt3rZq9",
        "[app] password: |\n[app]   hUnt3rZq9",
        "TS_AUTHKEY=hUnt3rZq9",
        "--api-key hUnt3rZq9",
        "--key hUnt3rZq9",
        "--auth-key=hUnt3rZq9",
        "stripe_key=hUnt3rZq9",
        "<password>hUnt3rZq9</password>",
    ],
)
def test_default_refs_plurals_prefixed_blocks_and_flags_are_redacted(text):
    out = redact_text(text)
    assert "hUnt3rZq9" not in out
    assert redact_text(out) == out


def test_auth_message_keeps_its_own_key_names():
    assert redact_text("auth: token=hUnt3rZq9 user=bob") == "auth: token=<redacted> user=bob"
    assert redact_with_count("auth: token=hUnt3rZq9 user=bob")[1] == 1


@pytest.mark.parametrize(
    "text",
    [
        "password=${env:DB_PASSWORD}",
        "password=${secrets.db}",
        "password=${DB_PASSWORD}",
        "auth: user=bob retries=3",
        "max_tokens=4096",
        "tokens=1234",
        "--keyboard us",
        "--key-file /etc/ssl/key.pem",
        "cookies: none",
        "data: |\n  plain",
        "2026-11-02T10:00:00Z data: |\n  plain",
    ],
)
def test_references_counts_and_settings_stay_readable(text):
    assert redact_text(text) == text


def test_block_ends_at_the_prefixed_key_indent():
    text = "web-1  |   password: |\nweb-1  |     l1\nweb-1  |   user: bob"
    out = "web-1  |   password: |\nweb-1  |     <redacted>\nweb-1  |   user: bob"
    assert redact_text(text) == out


def test_cut_note_is_a_line_of_its_own_and_never_redacted():
    for k in range(-20, 21):
        head = "a " * ((HALF + k - 3) // 2) + "PW=hunter2xyz " + "y " * 2500
        tail = "a " * 2500 + "PW=hunter2xyz " + "y " * ((HALF + k) // 2)
        for line in (head, tail):
            for out in (redact_text(line), guard_text("logs", line)[0]):
                assert "hunter2xyz" not in out, k
                assert re.search(r"^\[… \d+ chars cut\]$", out, re.M), k


def test_cut_line_inside_a_block_keeps_the_indent():
    text = "password: |\n    " + "x " * 3000 + "\nuser: bob"
    out = redact_text(text)
    assert "x x" not in out and out.endswith("user: bob")


def test_count_is_one_per_secret_kept():
    # a 4,001-character line: the two windows overlap, and the secret sits in both
    for pad in range(1700, 2300, 37):
        line = ("a " * 2100)[:pad] + " password=hunter2xyz "
        line += "y" * (4001 - len(line))
        out, count = redact_with_count(line)
        assert "hunter2xyz" not in out
        assert count == out.count("<redacted>") <= 1, pad
    # a secret dropped with the middle of a long line is not in the output and not counted
    out, count = redact_with_count("a " * 3000 + "password=hunter2xyz " + "b " * 3000)
    assert "hunter2xyz" not in out and count == 0
    out, count = redact_with_count("x " * 100 + "password=hunter2xyz " + "b " * 5000)
    assert count == 1 and redact_with_count(out) == (out, 0)


def test_many_short_secrets_on_a_cut_line_stay_idempotent():
    out, count = redact_with_count("pw=a " * 2000)
    assert "pw=a " not in out and redact_with_count(out) == (out, 0)
    assert count == out.count("<redacted>")


def _b64(n: int, seed: int) -> str:
    import base64
    import random

    rng = random.Random(seed)
    return base64.urlsafe_b64encode(bytes(rng.randrange(256) for _ in range(n))).decode()[:n]


@pytest.mark.parametrize("separators", [(",", ":"), (", ", ": ")])
def test_compact_json_token_on_the_tail_cut_is_gone(separators):
    payload, sig = _b64(900, 1), _b64(342, 2)
    token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9." + payload + "." + sig
    headers = {"host": "api", "user-agent": "x" * 1500, "x-access-token": token, "accept": "*/*"}
    record = {"level": "info", "msg": "upstream request", "req": {"headers": headers}}
    line = json.dumps({**record, "body": "y" * 1700}, separators=separators)
    out = guard_text("logs", line)[0]
    assert sig[-40:] not in out and payload[-60:-20] not in out


@pytest.mark.parametrize("key", ["access_token", "password", "session"])
@pytest.mark.parametrize("length", [1000, 2500, 4000])
def test_long_json_value_spanning_the_cut_middle_is_gone(key, length):
    secret = "eyJhbGciOiJSUzI1NiJ9.eyJ" + _b64(length, 7) + "." + _b64(342, 8)
    for pre in range(1000, 1900, 150):
        for post in range(0, 1900, 300):
            line = json.dumps({"a": "x" * pre, key: secret, "b": "y" * post}, separators=(",", ":"))
            out = guard_text("logs", line)[0]
            assert not any(secret[i : i + 16] in out for i in range(0, len(secret) - 16, 8)), pre


def test_cookie_value_spanning_the_cut_middle_is_gone():
    secret = _b64(2500, 3)
    for pre in range(1000, 1900, 150):
        line = "x" * pre + " Cookie: session=" + secret + ";theme=" + "y" * 900
        out = guard_text("logs", line)[0]
        assert not any(secret[i : i + 16] in out for i in range(0, len(secret) - 16, 8)), pre


def test_head_cut_moves_back_at_most_256_characters():
    line = '{"msg":"upstream request","pad":"' + "p" * 6000 + '"}'
    head = redact_text(line).split("\n")[0]
    assert len(head) >= HALF - 256


@pytest.mark.parametrize(
    "text",
    [
        "password=${DB_PASSWORD:hUnt3rZq9}",
        "spring.datasource.password=${DB_PASSWORD:hUnt3rZq9}",
        "password=${foo:hUnt3rZq9}",
        "aws_key=hUnt3rZq9",
        "googleKey: hUnt3rZq9",
        "access_tokens=hUnt3rZq9",
        "tokens: hUnt3rZq9",
        "Nov  2 10:00:00 host app: password: |\nNov  2 10:00:01 host app:   hUnt3rZq9",
    ],
)
def test_spring_defaults_vendor_keys_token_lists_and_syslog_blocks_are_redacted(text):
    assert "hUnt3rZq9" not in redact_text(text)


@pytest.mark.parametrize(
    "text",
    [
        "password=${env:DB_PASSWORD}",
        "password=${vault:db}",
        "cache_key=user:42",
        "s3_key=uploads/a.png",
        "partitionKey=tenant",
        "total_tokens=1234",
        "password_hashes=bcrypt",
    ],
)
def test_sources_identifier_keys_and_token_counts_stay_readable(text):
    assert redact_text(text) == text


LIST_KEYS = [f"k{i:02d}" + _b64(30, 100 + i).replace("-", "Z").replace("_", "Q") for i in range(12)]


@pytest.mark.parametrize(
    ("template", "joiner"),
    [
        ("API_KEYS={}", ","),
        ("VALID_TOKENS={}", ";"),
        ("api_keys: {}", ","),
        ('"api_keys":"{}"', ","),
        ('"api_keys": "{}"', ","),
        ('"api_keys":["{}"]', '","'),
        ('"refresh_tokens": ["{}"]', '", "'),
    ],
)
@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("a " * 1100 + " ", " " + "y " * 1000),
        ('{"pad":"' + "p" * 2200 + '",', ',"b":"' + "y" * 2000),
    ],
)
def test_a_secret_list_straddling_the_tail_cut_is_gone_whole(template, joiner, before, after):
    item = template.format(joiner.join(LIST_KEYS))
    for rest in range(HALF - 200, HALF + 20, 3):
        # the tail cut moves from the list's start to its end
        line = before + item + after[:rest]
        for out in (redact_text(line), guard_text("logs", line)[0]):
            assert not [k for k in LIST_KEYS if k[3:19] in out], rest


@pytest.mark.parametrize(
    ("text", "out"),
    [
        ('refresh_tokens: ["a1b2c3", "d4e5f6"]', "refresh_tokens: <redacted>"),
        ('{"api_keys": ["k1x", "k2y"], "user": "bob"}', '{"api_keys": <redacted>, "user": "bob"}'),
        ("api_keys: []", "api_keys: []"),
        ('api_keys: ["a1b2c3"], next=1', "api_keys: <redacted>, next=1"),
        ('{"api_keys":["k1","k2"],"user":"bob"}', '{"api_keys":<redacted>'),
        ('{"api_keys": ["k1", "k2"], "user": "bob"}', '{"api_keys": <redacted>, "user": "bob"}'),
        ('x: {"api_keys": ["k1x"]}', 'x: {"api_keys": <redacted>}'),
        ("password=[aB3],xY9kL2wq more", "password=<redacted> more"),
        ('msg="api_keys=[k1x]" level=info', 'msg="api_keys=<redacted>" level=info'),
        ("DB_PASSWORD=[aB3]xY9kL2wq", "DB_PASSWORD=<redacted>"),
        ("password=[none]xY9kL2wq", "password=<redacted>"),
        ("token ya29.a0AfH6SMBx3Kq9ZpLm2nQ7rT8u done", "token <redacted> done"),
        (
            '<password encrypted="false">hunter2</password>',
            '<password encrypted="false"><redacted></password>',
        ),
        ("<password>\n  hunter2\n</password>", "<password>\n  <redacted>\n</password>"),
        ('<add key="Password" value="hunter2"/>', '<add key="Password" value="<redacted>"/>'),
        ("<add key='Password' value='hunter2'/>", "<add key='Password' value='<redacted>'/>"),
    ],
)
def test_lists_google_tokens_and_xml_are_redacted_in_place(text, out):
    assert redact_with_count(text) == (out, out.count("<redacted>"))
    assert redact_with_count(out) == (out, 0)


def test_a_value_starting_with_a_bracket_hides_at_least_what_main_hid():
    """Before flow lists, an unquoted value under a secret key ran from its `[` to the next
    whitespace and was hidden whole. A random `[`-first value must still hide everything there."""
    import random

    rng = random.Random(58)
    alphabet = [*"[],;})]\"'aB3{(:= ", "TSEC", "none", "\n"]
    keys = ["password=", "DB_PASSWORD=", "api_keys: ", '"token": ', "secret => "]
    for _ in range(30_000):
        value = "[" + "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 12)))
        text = rng.choice(keys) + value + rng.choice(["", " next=1", '"}'])
        old_span = re.match(r"\S*", value).group(0)
        out = redact_text(text)
        if "TSEC" in old_span:
            assert out.count("TSEC") <= value[len(old_span) :].count("TSEC"), text
