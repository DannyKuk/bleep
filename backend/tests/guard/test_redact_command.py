"""The health check's command and probe output: what is hidden before the model reads them.

`redact_command` is for a line known to be a command. The global `redact_text`, which every log
line goes through, does not read client flags (see docs/design/guard.md).
"""

import json
import time

import pytest

from bleep.guard.redact import (
    hide_client_passwords,
    hide_values,
    redact_command,
    redact_text,
    secret_env_values,
)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("redis-cli -a s3cretpassw0rd ping", "redis-cli -a <redacted> ping"),
        ("redis-cli --pass s3cretpassw0rd ping", "redis-cli --pass <redacted> ping"),
        ("redis-cli -h cache -p 6379 -a 's3cretpassw0rd' ping", None),
        ("mongosh -p s3cretpassw0rd --quiet", "mongosh -p <redacted> --quiet"),
        ("mongosh --password s3cretpassw0rd --quiet", "mongosh --password <redacted> --quiet"),
        ("mongo -u root -p s3cretpassw0rd --eval 1", "mongo -u root -p <redacted> --eval 1"),
        ("mongo --password s3cretpassw0rd --eval 1", "mongo --password <redacted> --eval 1"),
        ("mongos -p s3cretpassw0rd", "mongos -p <redacted>"),
        ("curl -u user:s3cretpassw0rd http://x/health", "curl -u user:<redacted> http://x/health"),
        ("curl -uuser:s3cretpassw0rd http://x", "curl -uuser:<redacted> http://x"),
        ("curl --user bob:hunter2 x", "curl --user bob:<redacted> x"),
        ("mysqladmin -uroot -ps3cretpassw0rd ping", "mysqladmin -uroot -p<redacted> ping"),
        ("mysqladmin ping -uroot -p'secretpw'", "mysqladmin ping -uroot -p<redacted>"),
        ('mysqladmin ping -uroot -p"secretpw"', "mysqladmin ping -uroot -p<redacted>"),
        ("mysqladmin ping -uroot -p'pa ss'", "mysqladmin ping -uroot -p<redacted>"),
        ("mysqladmin ping -uroot -ppa,ss", "mysqladmin ping -uroot -p<redacted>"),
        ("redis-cli -a pa,ss ping", "redis-cli -a <redacted> ping"),
        ("redis-cli -a pa],ss ping", "redis-cli -a <redacted> ping"),
        ("redis-cli -a 'pa ss' ping", "redis-cli -a '<redacted>' ping"),
        ("curl -u user:pa,ss http://x", "curl -u user:<redacted> http://x"),
        ('curl -u "user:pa ss" http://x', 'curl -u "user:<redacted>" http://x'),
        ("echo up; redis-cli -a pw1234 ping", "echo up; redis-cli -a <redacted> ping"),
        ("curl -sS x && redis-cli -a pw1234 ping", "curl -sS x && redis-cli -a <redacted> ping"),
        ("/usr/bin/redis-cli -a pw1234 ping", "/usr/bin/redis-cli -a <redacted> ping"),
        ('redis-cli -a "pa ss ping', "redis-cli -a <redacted>"),
        ('curl "abc -u bob:pw1234', "curl <redacted>"),
    ],
)
def test_redact_command_hides_a_client_password_however_it_is_written(line, expected):
    out = redact_command(line)
    if expected is not None:
        assert out == expected
    assert "s3cretpassw0rd" not in out and "pw1234" not in out
    assert redact_command(out) == out


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            '["redis-cli", "-a", "s3cretpassw0rd", "ping"]',
            '["redis-cli", "-a", "<redacted>", "ping"]',
        ),
        ('["redis-cli", "-a", "pa,ss", "ping"]', '["redis-cli", "-a", "<redacted>", "ping"]'),
        ('["mongo", "-p", "s3cretpassw0rd"]', '["mongo", "-p", "<redacted>"]'),
        (
            '["curl", "-u", "user:pa,ss", "http://x"]',
            '["curl", "-u", "user:<redacted>", "http://x"]',
        ),
        ('["mysqladmin", "-ps3cretpassw0rd", "ping"]', '["mysqladmin", "-p<redacted>", "ping"]'),
        (
            '["sh", "-c", "redis-cli -a pw1234 ping"]',
            '["sh", "-c", "redis-cli -a <redacted> ping"]',
        ),
        (
            '["sh", "-c", "redis-cli -a pw1234 ping", "x"]',
            '["sh", "-c", "redis-cli -a <redacted> ping", "x"]',
        ),
    ],
)
def test_redact_command_reads_the_json_list_form(line, expected):
    out = redact_command(line)
    assert out == expected
    assert json.loads(out)
    assert redact_command(out) == out


@pytest.mark.parametrize(
    "line",
    [
        "curl: (7) Failed to connect to localhost port 8000",
        "curl -sS http://localhost:8000/health -o /dev/null",
        "curl -u bob http://x",
        "mongodb://h:27017/db",
        "mongo-express started on port 8081",
        "ls -a",
        "redis-cli -p 6379 ping",
        "mysqladmin -P3306 -p mydb ping",
        "pg_isready -U shop -d shop",
        "mongosh -p",
    ],
)
def test_redact_command_leaves_a_normal_command_alone(line):
    assert redact_command(line) == line


@pytest.mark.parametrize(
    "line",
    [
        '{"cmd":"curl -u bob:pw1234","status":500}',
        '{"cmd":"redis-cli -a pw1234 ping","exit":1,"error":"NOAUTH"}',
        "curl -u bob:pw1234 http://x",
    ],
)
def test_redact_text_does_not_read_client_flags(line):
    """Ordinary log lines are not read as command lines: a JSON log record stays JSON."""
    assert redact_text(line) == line
    if line.startswith("{"):
        assert json.loads(redact_text(line))


@pytest.mark.parametrize("unit", ['\\"', "\\'", '"', "'", '"\\', "a '", 'x "y'])
@pytest.mark.parametrize("client", ["curl", "redis-cli", "mysqladmin", "mongo"])
@pytest.mark.parametrize("json_form", [False, True])
def test_hide_client_passwords_is_linear_on_a_million_characters(client, unit, json_form):
    text = (f'["{client}", ' if json_form else f"{client} ") + unit * (1_000_000 // len(unit))
    started = time.perf_counter()
    hide_client_passwords(text)
    assert time.perf_counter() - started < 1.0


def test_redact_command_is_fast_on_a_million_characters_of_quotes():
    text = "curl " + '\\"' * 500_000
    started = time.perf_counter()
    redact_command(text)
    assert time.perf_counter() - started < 1.0


def test_secret_env_values_are_the_values_the_redaction_hides_longest_first():
    env = [
        "PORT=8000",
        "REDIS_PASSWORD=s3cretpassw0rd",
        "API_KEY=abc999",
        "LEVEL=info",
        "NOVALUE",
        "EMPTY_SECRET=",
        "DATABASE_URL=postgresql://shop:pw1234@db:5432/shop",
    ]
    values = secret_env_values(env)
    assert "8000" not in values and "info" not in values
    assert values[0] == "postgresql://shop:pw1234@db:5432/shop"
    assert {"s3cretpassw0rd", "abc999"} <= set(values)
    assert hide_values("redis-cli -a s3cretpassw0rd --port 8000", values) == (
        "redis-cli -a <redacted> --port 8000"
    )


def test_hide_values_never_cuts_a_short_value_out_of_a_word():
    values = ["e"]
    assert hide_values("TimeoutError: timed out", values) == "TimeoutError: timed out"
    assert hide_values("key e here", values) == "key <redacted> here"
    assert hide_values("--key=e", values) == "--key=<redacted>"


def test_hide_values_makes_one_pass_so_a_marker_is_never_rewritten():
    assert hide_values("ed t <redacted>", ["ed", "t"]) == "<redacted> <redacted> <redacted>"
    assert hide_values("a <redacted> b", ["redacted"]) == "a <redacted> b"
    once = hide_values("pw=abc-def abc", ["abc", "abc-def"])
    assert once == "pw=<redacted> <redacted>"
    assert hide_values(once, ["abc", "abc-def"]) == once


def test_hide_values_hides_a_long_value_inside_a_word_but_a_short_one_only_whole():
    assert hide_values("mysqladmin -ps3cretpassw0rd", ["s3cretpassw0rd"]) == (
        "mysqladmin -p<redacted>"
    )
    assert hide_values("abcdefg abcdefgh", ["abcdefg"]) == "<redacted> abcdefgh"
    assert hide_values("shopping", ["shop"]) == "shopping"


def test_hide_values_hides_a_password_that_is_also_a_plain_word_in_the_command():
    shop = '["pg_isready", "-U", "shop", "-d", "shop"]'
    assert hide_values(shop, ["shop"]) == '["pg_isready", "-U", "<redacted>", "-d", "<redacted>"]'
