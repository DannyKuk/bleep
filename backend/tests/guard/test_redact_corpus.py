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
            tail = cut_points(line, HALF, whole_words=True)[1]
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
