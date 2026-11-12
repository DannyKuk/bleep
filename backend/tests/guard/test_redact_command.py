"""A client's command-line password, and the health check's command and probe output: what is
hidden before the model reads them.

`redact_command` is for a line known to be a command. The global `redact_text`, which every log
line goes through, reads client flags too, but as free text: a JSON log record stays JSON and a
quote nothing closes does not hide the rest of an ordinary line (see docs/design/guard.md).
"""

import json
import time

import pytest

from bleep.guard.redact import (
    hide_client_passwords,
    hide_values,
    redact_command,
    redact_text,
    redact_with_count,
    secret_env_values,
)
from bleep.guard.sanitize import MAX_LINE


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


def _hidden_once(line: str) -> str:
    out, count = redact_with_count(line)
    assert count >= 1
    assert redact_with_count(out) == (out, 0)
    return out


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("+ redis-cli -a s3cretpassw0rd ping", "+ redis-cli -a <redacted> ping"),
        ("redis-cli --pass s3cretpassw0rd ping", "redis-cli --pass <redacted> ping"),
        ("mongo -u root -p s3cretpassw0rd --eval 1", "mongo -u root -p <redacted> --eval 1"),
        ("mongos -p s3cretpassw0rd", "mongos -p <redacted>"),
        ("mongosh --password s3cretpassw0rd --quiet", "mongosh --password <redacted> --quiet"),
        ("curl -u user:s3cretpassw0rd https://x/h", "curl -u user:<redacted> https://x/h"),
        ("curl --user user:s3cretpassw0rd https://x/h", "curl --user user:<redacted> https://x/h"),
        ("mysqladmin -ps3cretpassw0rd ping", "mysqladmin -p<redacted> ping"),
        ("mysqladmin -p'secret pw' ping", "mysqladmin -p<redacted> ping"),
        ("mysql -u root -phUnt3rZq9", "mysql -u root -p<redacted>"),
        ("redis-cli -a 'pa ss' ping", "redis-cli -a '<redacted>' ping"),
        ('redis-cli -a pa"ss ping', "redis-cli -a <redacted>"),
        (
            "2026-11-12T03:00:00Z cron: curl -u bob:pw1234 http://x",
            "2026-11-12T03:00:00Z cron: curl -u bob:<redacted> http://x",
        ),
        ('msg="curl -u bob:pw1234" status=500', 'msg="curl -u bob:<redacted>" status=500'),
        ('+ curl -u bob:pw1234 -d {"a":1} x', '+ curl -u bob:<redacted> -d {"a":1} x'),
        (
            "['redis-cli', '-a', 'pw1234', 'ping']",
            "['redis-cli', '-a', '<redacted>', 'ping']",
        ),
        ("run `redis-cli -a pw1234` first.", "run `redis-cli -a <redacted>` first."),
        ('ran "curl -u bob:pw1234".', 'ran "curl -u bob:<redacted>".'),
        ("'step\\ncurl -u bob:pw1234'", "'step\\ncurl -u bob:<redacted>'"),
        ("mysqladmin -uroot -p'mysql' ping", "mysqladmin -uroot -p<redacted> ping"),
        ('mysql -p"mariadb"', "mysql -p<redacted>"),
        ("curl -ubob:'mongo' x", "curl -ubob:<redacted> x"),
        ("mysqladmin -p'.S3cret pw' ping", "mysqladmin -p<redacted> ping"),
        ("curl -u bob:',pw 1234' x", "curl -u bob:<redacted> x"),
    ],
)
def test_redact_text_hides_a_client_password_in_a_log_line(line, expected):
    assert _hidden_once(line) == expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            '{"cmd":"curl -u bob:pw1234","status":500}',
            '{"cmd":"curl -u bob:<redacted>","status":500}',
        ),
        (
            '{"cmd":"redis-cli -a pw1234 ping","exit":1,"error":"NOAUTH"}',
            '{"cmd":"redis-cli -a <redacted> ping","exit":1,"error":"NOAUTH"}',
        ),
        (
            '{"cmd": "redis-cli --pass pw1234", "level": "info"}',
            '{"cmd": "redis-cli --pass <redacted>", "level": "info"}',
        ),
        (
            '{"cmd": "mongo -u root -p pw1234 --eval 1", "level": "info"}',
            '{"cmd": "mongo -u root -p <redacted> --eval 1", "level": "info"}',
        ),
        (
            '{"cmd":"curl --user bob:pw1234 http://x","n":1}',
            '{"cmd":"curl --user bob:<redacted> http://x","n":1}',
        ),
        (
            '{"cmd":"mysqladmin -ppw1234 ping","n":1}',
            '{"cmd":"mysqladmin -p<redacted> ping","n":1}',
        ),
        (
            '{"cmd":"mysqladmin -p\'secret pw1234\' ping","n":1}',
            '{"cmd":"mysqladmin -p<redacted> ping","n":1}',
        ),
        (
            '{"cmd":"redis-cli -a pa\\"ss1234 ping","n":1}',
            '{"cmd":"redis-cli -a <redacted>","n":1}',
        ),
        (
            '{"cmd":"curl -u \\"bob:pa ss1234\\" x","n":1}',
            '{"cmd":"curl -u \\"bob:<redacted>\\" x","n":1}',
        ),
        (
            '{"cmd":"curl -u b\\u00f6b:pw1234 \\/x\\nnext","n":1}',
            '{"cmd":"curl -u b\\u00f6b:<redacted> \\/x\\nnext","n":1}',
        ),
        (
            '{"log":"step 1\\ncurl -u bob:pw1234 x","n":1}',
            '{"log":"step 1\\ncurl -u bob:<redacted> x","n":1}',
        ),
        (
            '{"log":"step 1\\fcurl -u bob:pw1234 x\\u0020mongo -p pw1234","n":1}',
            '{"log":"step 1\\fcurl -u bob:<redacted> x\\u0020mongo -p <redacted>","n":1}',
        ),
        (
            '{"a":"redis-cli -a pw1234 ping","b":"curl -u bob:pw1234 x"}',
            '{"a":"redis-cli -a <redacted> ping","b":"curl -u bob:<redacted> x"}',
        ),
        (
            'web-1  | {"cmd":"curl -u bob:pw1234","status":500}',
            'web-1  | {"cmd":"curl -u bob:<redacted>","status":500}',
        ),
        (
            '{"argv":["redis-cli","-a","pw1234","ping"],"n":1}',
            '{"argv":["redis-cli","-a","<redacted>","ping"],"n":1}',
        ),
        (
            '{"job":{"argv":["curl", "-u", "bob:pw1234"]},"status":500}',
            '{"job":{"argv":["curl", "-u", "bob:<redacted>"]},"status":500}',
        ),
        (
            '["sh", "-c", "redis-cli -a pw1234 ping"]',
            '["sh", "-c", "redis-cli -a <redacted> ping"]',
        ),
    ],
)
def test_redact_text_hides_a_client_password_inside_a_json_string(line, expected):
    """A word inside a JSON string ends at the string's closing quote, escaped quotes included:
    the record stays JSON with its other keys intact."""
    out = _hidden_once(line)
    assert out == expected
    json.loads(out.removeprefix("web-1  | "))
    assert "pw1234" not in out and "ss1234" not in out


def test_a_pretty_printed_member_line_keeps_its_comma():
    assert _hidden_once('  "cmd": "curl -u bob:pw1234",') == '  "cmd": "curl -u bob:<redacted>",'


@pytest.mark.parametrize(
    "line",
    [
        "curl: (7) Failed to connect to localhost port 8000",
        "curl: (6) Couldn't resolve host 'x'",
        "curl -sS http://localhost:8000/health -o /dev/null",
        "curl -u bob http://x",
        'curl failed: "timeout',
        "mongo said 'x",
        "mongodb://mongo:27017/db",
        "mongo-express started on port 8081",
        "ls -a",
        "redis-cli -p 6379 ping",
        '{"msg":"curl failed: \\"timeout","n":1}',
        '{"argv":["redis-cli","-a"],"n":"x"}',
        '{"curl":"-u","x":"bob:pw"}',
        "mysql -u root -p mydb",
        "docker run -d --name mongo -p 27017:27017 mongo:7",
        "docker run --name mongo -p 127.0.0.1:27017:27017 mongo",
        "redis-cli -a $REDIS_PASSWORD ping",
        'redis-cli -a "$REDIS_PASSWORD" ping',
        "mysqladmin -p${DB_PASSWORD} ping",
        '{"cmd":"curl -u bob:${API_PASSWORD} x","n":1}',
    ],
)
def test_redact_text_leaves_an_ordinary_line_with_a_client_name_alone(line):
    assert redact_with_count(line) == (line, 0)


@pytest.mark.parametrize(
    "command",
    ["redis-cli -a pw1234XYZ ping", 'mysqladmin -p"pw1234XYZ" ping', "curl -u b:pw1234XYZ"],
)
def test_a_password_across_the_cut_of_a_long_line_is_hidden(command):
    for offset in range(-80, 80, 3):
        line = "a " * 2500 + "b" * (offset % 2) + " " + command + " " + "y " * (920 + offset // 2)
        assert len(line) > MAX_LINE
        assert "1234XYZ" not in redact_text(line), offset


@pytest.mark.parametrize("unit", ['\\"', "\\'", '"', "'", '"\\', "a '", 'x "y'])
@pytest.mark.parametrize("client", ["curl -u a:", "redis-cli -a ", "mysqladmin -p"])
def test_redact_text_is_linear_on_one_line_of_a_million_characters(client, unit):
    text = client + unit * (1_000_000 // len(unit))
    started = time.perf_counter()
    redact_text(text)
    assert time.perf_counter() - started < 1.0


def _lines_of(line: str) -> str:
    """About 1 MB of `line`, each line under the cut, so every one is read in full."""
    assert len(line) < MAX_LINE
    return "\n".join([line] * (1_000_000 // (len(line) + 1)))


def _hostile_line(client: str, unit: str, form: str) -> str:
    units = unit * ((MAX_LINE - 200) // (2 * len(unit)))
    if form == "plain":
        return f"{client} -u a:{units}"
    if form == "json":
        return json.dumps({"cmd": f"{client} -a {units}", "n": 1}, separators=(",", ":"))
    return f'["{client}", ' + units


@pytest.mark.parametrize("unit", ['\\"', "\\'", '"', "'", '"\\', "a '", 'x "y'])
@pytest.mark.parametrize("client", ["curl", "redis-cli", "mysqladmin", "mongo"])
@pytest.mark.parametrize("form", ["plain", "json", "list"])
def test_redact_text_is_linear_on_a_million_characters_of_client_lines(client, unit, form):
    text = _lines_of(_hostile_line(client, unit, form))
    started = time.perf_counter()
    redact_text(text)
    assert time.perf_counter() - started < 1.0


@pytest.mark.parametrize(
    "line",
    [
        "curl " * 790,
        'curl " ' * 560,
        "redis-cli -a " * 300,
        json.dumps({"cmd": 'curl \\" ' * 380}),
        json.dumps({"cmd": "redis-cli -a " * 300}),
        json.dumps(["curl"] * 450),
        json.dumps([f"curl -u a:{n}" for n in range(230)]),
    ],
)
def test_redact_text_is_linear_on_lines_full_of_client_names(line):
    text = _lines_of(line)
    started = time.perf_counter()
    redact_text(text)
    assert time.perf_counter() - started < 1.0


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
