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


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            'INFO curl check {"cmd":"curl -u bob:pw1234XY","n":1}',
            'INFO curl check {"cmd":"curl -u bob:<redacted>","n":1}',
        ),
        ('curl ok {"cmd":"redis-cli -a pw1234XY"}', 'curl ok {"cmd":"redis-cli -a <redacted>"}'),
        (
            '{"tool":"curl","cmd":"curl -u a:pw1234XY","n":1}',
            '{"tool":"curl","cmd":"curl -u a:<redacted>","n":1}',
        ),
        (
            '{"curl":1,"cmd":"redis-cli -a pw1234XY","n":1}',
            '{"curl":1,"cmd":"redis-cli -a <redacted>","n":1}',
        ),
        (
            '{"cmd":"curl -u bob:pw1234XY","msg":"curl -u al:pw1234XY"}',
            '{"cmd":"curl -u bob:<redacted>","msg":"curl -u al:<redacted>"}',
        ),
        (
            'mongo started; cmd="redis-cli -a pw1234XY"',
            'mongo started; cmd="redis-cli -a <redacted>"',
        ),
        (
            'curl -s x && sh -c "redis-cli -a pw1234XY ping"',
            'curl -s x && sh -c "redis-cli -a <redacted> ping"',
        ),
        ('curl -s x; echo "curl -u a:pw1234XY"', 'curl -s x; echo "curl -u a:<redacted>"'),
        (
            'mysql said: run "curl -u a:pw1234XY" now',
            'mysql said: run "curl -u a:<redacted>" now',
        ),
        ("redis-cli -a mongo ping", "redis-cli -a <redacted> ping"),
        (
            "curl -u bob:pw1234XY && redis-cli -a pw1234XY",
            "curl -u bob:<redacted> && redis-cli -a <redacted>",
        ),
    ],
)
def test_every_client_command_on_a_line_is_read(line, expected):
    """A line is read in pieces, from each client's name to the next, so an earlier name, a key
    or a quote never hides a later command from the reading."""
    out = _hidden_once(line)
    assert out == expected
    if "{" in line:
        json.loads(out[out.index("{") :])


# A value right after a client's password flag is its password, whatever it looks like.
PASSWORDS_IN_ANY_SHAPE = [
    ("redis-cli -a 123456 ping", "redis-cli -a <redacted> ping"),
    ("redis-cli -a 2024:01:01 ping", "redis-cli -a <redacted> ping"),
    ("curl -u bob:123456 http://x", "curl -u bob:<redacted> http://x"),
    ("curl -u bob:123456:654321 http://x", "curl -u bob:<redacted> http://x"),
    ("mysqladmin -p123456 ping", "mysqladmin -p<redacted> ping"),
    ("mysql -p=s3cretpw db", "mysql -p<redacted> db"),
    ("mysqlsh -ps3cretpw", "mysqlsh -p<redacted>"),
    ("mongo -ps3cretpw --eval 1", "mongo -p<redacted> --eval 1"),
    ("mongosh -p 27017 --quiet", "mongosh -p <redacted> --quiet"),
    ("redis-cli -a -s3cretpw ping", "redis-cli -a <redacted> ping"),
    ("redis-cli -a s3cret\\ pass ping", "redis-cli -a <redacted> ping"),
    ("curl -U proxy:s3cretpw http://x", "curl -U proxy:<redacted> http://x"),
    ("curl --proxy-user proxy:s3cretpw http://x", "curl --proxy-user proxy:<redacted> http://x"),
    ("curl -su bob:s3cretpw http://x", "curl -su bob:<redacted> http://x"),
    ("curl -fsSu bob:s3cretpw http://x", "curl -fsSu bob:<redacted> http://x"),
    ("curl -sSubob:s3cretpw http://x", "curl -sSubob:<redacted> http://x"),
]


@pytest.mark.parametrize(("line", "expected"), PASSWORDS_IN_ANY_SHAPE)
def test_redact_text_hides_a_password_whatever_it_looks_like(line, expected):
    assert _hidden_once(line) == expected


@pytest.mark.parametrize(("line", "expected"), PASSWORDS_IN_ANY_SHAPE)
def test_redact_command_hides_a_health_check_password_whatever_it_looks_like(line, expected):
    assert redact_command(line) == expected
    assert redact_command(expected) == expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            '{"cmd":"redis-cli -a s3cret\\\\ pass ping","n":1}',
            '{"cmd":"redis-cli -a <redacted> ping","n":1}',
        ),
        ('{"cmd":"redis-cli -a 123456","n":1}', '{"cmd":"redis-cli -a <redacted>","n":1}'),
        ('["CMD", "redis-cli", "-a", "123456"]', '["CMD", "redis-cli", "-a", "<redacted>"]'),
        ('{"argv":["curl","-U","p:123456"],"n":1}', '{"argv":["curl","-U","p:<redacted>"],"n":1}'),
    ],
)
def test_a_password_in_any_shape_is_hidden_inside_json(line, expected):
    out = _hidden_once(line)
    assert out == expected
    json.loads(out)
    assert redact_command(line) == expected


@pytest.mark.parametrize(
    "line",
    [
        "docker run --name mongo -p 27017:27017/tcp mongo",
        "docker run --name mongo -p 0.0.0.0:27017:27017/udp mongo",
        "mongosh -u root -p --authenticationDatabase admin",
        "mysqladmin -p ping",
        "mongosh -p --quiet",
    ],
)
def test_a_port_mapping_or_a_missing_password_stays(line):
    assert redact_with_count(line) == (line, 0)
    assert redact_command(line) == line


def _in_form(command: str, form: str) -> str:
    if form == "json":
        return json.dumps({"cmd": command, "n": 1})
    if form == "logfmt":
        return _logfmt(command) + " n=1"
    if form == "logfmt twice":
        return _logfmt(_logfmt(command)) + " n=1"
    return command


@pytest.mark.parametrize(
    "mapping", ["27017:27017", "27017:27017/tcp", "127.0.0.1:27017:27017/udp", ":27017"]
)
@pytest.mark.parametrize("quote", ['"', "'"])
@pytest.mark.parametrize("form", ["plain", "json", "logfmt", "logfmt twice"])
def test_a_quoted_port_mapping_after_mongo_p_stays_inside_escaped_quotes(mapping, quote, form):
    """`msg="docker run --name mongo -p \\"27017:27017\\" mongo"`: the escaped quotes around the
    mapping are no part of it (#510)."""
    line = _in_form(f"docker run --name mongo -p {quote}{mapping}{quote} mongo", form)
    assert redact_with_count(line) == (line, 0)


@pytest.mark.parametrize(
    "password", ["27017:27017x", "27017:27017 s3cret", "27017:27017/s3cret", "s3cret:27017"]
)
@pytest.mark.parametrize("quote", ['"', "'"])
@pytest.mark.parametrize("form", ["plain", "json", "logfmt", "logfmt twice"])
def test_a_password_that_only_looks_like_a_port_mapping_is_hidden_in_escaped_quotes(
    password, quote, form
):
    line = _in_form(f"docker run --name mongo -p {quote}{password}{quote} mongo", form)
    out = _hidden_once(line)
    assert "s3cret" not in out and "27017x" not in out
    assert out.count("mongo") == line.count("mongo")


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ('mongo -u mongo -p "mongo" --eval 1', 'mongo -u mongo -p "<redacted>" --eval 1'),
        ("redis-cli -a 'mongo' ping", "redis-cli -a '<redacted>' ping"),
        ('redis-cli --pass "mysql"', 'redis-cli --pass "<redacted>"'),
        ("curl -u 'bob:mysql' x", "curl -u 'bob:<redacted>' x"),
        (
            'redis-cli -a "pass phrase with curl inside" ping',
            'redis-cli -a "<redacted>" ping',
        ),
        ("redis-cli -a 's3c mysql ret' ping", "redis-cli -a '<redacted>' ping"),
        ("curl -u bob:'x mysql -ps3cret2'", "curl -u bob:<redacted>"),
        ("mysqladmin -p'pw mongo -p x' ping", "mysqladmin -p<redacted> ping"),
        ("redis-cli -a --s3cret ping", "redis-cli -a <redacted> ping"),
        (
            '{"cmd":"redis-cli -a \'s3c mysql ret\' ping","n":1}',
            '{"cmd":"redis-cli -a \'<redacted>\' ping","n":1}',
        ),
        (
            '{"cmd":"redis-cli -a \\"pass with curl inside\\" ping","n":1}',
            '{"cmd":"redis-cli -a \\"<redacted>\\" ping","n":1}',
        ),
        ('{"cmd":"mongo -p\'mongo\' x","n":1}', '{"cmd":"mongo -p<redacted> x","n":1}'),
    ],
)
def test_a_quoted_password_holding_a_client_name_is_hidden_whole(line, expected):
    """The names inside a quoted password are part of it, not commands of their own."""
    out = _hidden_once(line)
    assert out == expected
    assert "s3c" not in out and "phrase" not in out
    if line.startswith("{"):
        json.loads(out)
    else:
        assert redact_command(line) == expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            'msg="redis-cli -a \\"pa ss\\" ping" status=500',
            'msg="redis-cli -a \\"<redacted>\\" ping" status=500',
        ),
        (
            'time=x level=info msg="exec: curl -u \\"bob:my pass\\" http://x"',
            'time=x level=info msg="exec: curl -u \\"bob:<redacted>\\" http://x"',
        ),
        (
            'level=info msg="mysqladmin -p\\"pa ss\\" ping"',
            'level=info msg="mysqladmin -p<redacted> ping"',
        ),
        (
            "msg=\"redis-cli -a \\'pa ss\\' ping\" status=500",
            "msg=\"redis-cli -a \\'<redacted>\\' ping\" status=500",
        ),
        (
            'msg="redis-cli -a \\"pass with curl inside\\" ping" status=500',
            'msg="redis-cli -a \\"<redacted>\\" ping" status=500',
        ),
        (
            'msg="run \\"curl -u a:pw1234\\" now" n=1',
            'msg="run \\"curl -u a:<redacted>\\" now" n=1',
        ),
        ('msg="redis-cli -a \\"pa ss ping" status=500', 'msg="redis-cli -a <redacted>'),
    ],
)
def test_a_password_between_escaped_quotes_in_logfmt_is_hidden_whole(line, expected):
    """logfmt (logrus, slog, the Docker daemon) escapes every quote in a value: an escaped quote
    opens and closes a password as a quote does, and the line's own quotes and keys stay."""
    out = _hidden_once(line)
    assert out == expected
    assert "ss" not in out.replace("pass", "") and "my pass" not in out


def _logfmt(text: str) -> str:
    return 'msg="' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


TWICE_ESCAPED_COMMANDS = [
    'redis-cli -a " s3cret" ping',
    'redis-cli -a ";s3cret" ping',
    'redis-cli -a ",s3cret x" ping',
    'redis-cli -a ")s3cret" ping',
    'mongo -p" s3cret" --quiet',
    'mongosh -u root -p ":s3cret" --eval 1',
    'curl -u "bob: s3cret" http://x/h',
    'mysqladmin -uroot -p".s3cret" ping',
]


@pytest.mark.parametrize("command", TWICE_ESCAPED_COMMANDS)
@pytest.mark.parametrize(
    "form",
    [
        lambda c: _logfmt(_logfmt(c)) + " status=500",
        lambda c: _logfmt(json.dumps({"cmd": c})) + " status=500",
        lambda c: json.dumps({"log": _logfmt(_logfmt(c)), "status": 500}),
        lambda c: json.dumps({"log": _logfmt(json.dumps({"cmd": c})), "status": 500}),
        lambda c: _logfmt(_logfmt(_logfmt(c))) + " status=500",
    ],
    ids=["logfmt in logfmt", "json in logfmt", "in json", "json in logfmt in json", "thrice"],
)
def test_a_password_between_quotes_escaped_twice_is_hidden_whole(command, form):
    """A run of backslashes and a quote (`\\\\\\"`) is one quote, closed by the same run, so a
    password that starts with a blank or punctuation is not cut at the run's backslashes."""
    line = form(command)
    out = _hidden_once(line)
    assert "s3cret" not in out
    assert "status" in out
    if line.startswith("{"):
        assert json.loads(out).keys() == json.loads(line).keys()


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            'msg="msg=\\"redis-cli -a \\\\\\" s3cret\\\\\\" ping\\""',
            'msg="msg=\\"redis-cli -a \\\\\\"<redacted>\\\\\\" ping\\""',
        ),
        (
            'msg="{\\"cmd\\": \\"redis-cli -a \\\\\\" s3cret\\\\\\" ping\\"}"',
            'msg="{\\"cmd\\": \\"redis-cli -a \\\\\\"<redacted>\\\\\\" ping\\"}"',
        ),
        (
            'msg="msg=\\"mongo -p\\\\\\" s3cret\\\\\\" --quiet\\""',
            'msg="msg=\\"mongo -p<redacted> --quiet\\""',
        ),
        (
            'redis-cli -a \\"a \\\\\\"b\\\\\\" c\\" ping',
            'redis-cli -a \\"<redacted>\\" ping',
        ),
        (
            'msg="curl -u \\"bob:s3cret\\\\\\\\\\" http://x" status=500',
            'msg="curl -u \\"bob:<redacted>\\" http://x" status=500',
        ),
        (
            'msg="redis-cli -a \\" s3cret\\\\\\\\\\" ping" status=500',
            'msg="redis-cli -a \\"<redacted>\\" ping" status=500',
        ),
        (
            "msg='redis-cli -a \\' s3cret\\\\\\' ping' status=500",
            "msg='redis-cli -a \\'<redacted>\\' ping' status=500",
        ),
        (
            'msg="msg=\\"redis-cli -a \\\\\\" s3cret\\\\\\\\\\\\\\\\\\\\\\" ping\\"" n=1',
            'msg="msg=\\"redis-cli -a \\\\\\"<redacted>\\\\\\" ping\\"" n=1',
        ),
        (
            'msg="redis-cli -a \\"pw\\\\\\\\\\"\'\\" s3cret\' ping" n=1',
            'msg="redis-cli -a \\"<redacted>\\" ping" n=1',
        ),
        (
            _logfmt(_logfmt('redis-cli -a "pw\\\\"\'" s3cret\' ping')) + " n=1",
            'msg="msg=\\"redis-cli -a \\\\\\"<redacted>\\\\\\" ping\\"" n=1',
        ),
    ],
)
def test_an_escaped_quote_is_closed_by_a_run_the_shell_would_close_it_with(line, expected):
    """A narrower run closes the string around the command, and a wider one of a double quote
    holds an escaped quote of the password (`\\\\\\"` in `\\"`), but one that adds an even number of
    the password's backslashes (`pw\\\\\\\\\\"`, a password ending in a backslash) closes it."""
    assert _hidden_once(line) == expected
    in_json = redact_text(json.dumps({"log": line, "n": 1}))
    assert "s3cret" not in in_json and json.loads(in_json)["n"] == 1


@pytest.mark.parametrize(
    ("hide", "line", "expected"),
    [
        (redact_text, 'msg="redis-cli -a pw\\\\" n=1', 'msg="redis-cli -a <redacted>" n=1'),
        (redact_command, 'redis-cli -a \\\\"pw x" ping', "redis-cli -a <redacted> ping"),
        (hide_client_passwords, 'redis-cli -a \\\\"pw x" ping', "redis-cli -a <redacted> ping"),
        (
            hide_client_passwords,
            'redis-cli -a \\\\\\"pw\\\\\\" ping',
            'redis-cli -a \\\\\\"<redacted>\\\\\\" ping',
        ),
        (
            hide_client_passwords,
            'redis-cli -a \\\\\\"pw x\\\\\\" ping',
            'redis-cli -a \\\\\\"<redacted>\\\\\\" x\\\\\\" ping',
        ),
    ],
)
def test_a_run_of_backslashes_and_a_quote_keeps_its_backslashes_around_the_marker(
    hide, line, expected
):
    """What reading a run as one quote changes: a password's trailing backslashes before the
    plain quote that closes the string around the command are hidden with it, as only that quote
    opened a string before the command (#509); an odd run (`\\\\"`, an escaped backslash and a
    quote) opens no wide quote, so the word is hidden whole as before; and a wide quote that
    opens the password is put back after `<redacted>`."""
    out = hide(line)
    assert out == expected
    assert hide(out) == out


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            'msg="{\\"cmd\\": \\"redis-cli -a \\\\u0022 s3cret\\\\u0022 ping\\"}"',
            'msg="{\\"cmd\\": \\"redis-cli -a \\\\u0022<redacted>\\\\u0022 ping\\"}"',
        ),
        (
            '{"cmd": "redis-cli -a \\u0022 s3cret\\u0022 ping"}',
            '{"cmd": "redis-cli -a \\u0022<redacted>\\u0022 ping"}',
        ),
        (
            "INFO run redis-cli -a \\u0022 s3cret\\u0022 ping now",
            "INFO run redis-cli -a \\u0022<redacted>\\u0022 ping now",
        ),
        (
            'msg="{\\"cmd\\": \\"curl -u \\\\u0027bob: s3cret\\\\u0027 http://x\\"}" n=1',
            'msg="{\\"cmd\\": \\"curl -u \\\\u0027bob:<redacted>\\\\u0027 http://x\\"}" n=1',
        ),
        (
            'cmd=\'redis-cli -a "a\\\\" s3cret" ping\' n=1',
            "cmd='redis-cli -a \"<redacted>\" ping' n=1",
        ),
        (
            "msg='redis-cli -a \" s3cret\\\\\\\\\" ping' n=1",
            "msg='redis-cli -a \"<redacted>\" ping' n=1",
        ),
        (
            _logfmt("cmd=" + repr('redis-cli -a "a\\" s3cret" ping')) + " n=1",
            'msg="cmd=\'redis-cli -a \\"<redacted>\\" ping\'" n=1',
        ),
    ],
)
def test_a_quote_written_as_u0022_or_left_as_it_is_under_a_single_quoting_level_is_hidden(
    line, expected
):
    """.NET's System.Text.Json writes `"` as `\\u0022` (`\\\\u0022` in logfmt), a quote whose
    backslashes double with each level around it; a level that quotes with `'` (single-quoted
    logfmt, `repr`) leaves `"` as it is and doubles the backslash that escapes it (`\\\\"`)."""
    assert _hidden_once(line) == expected
    in_json = redact_text(json.dumps({"log": line, "n": 1}))
    assert "s3cret" not in in_json and json.loads(in_json)["n"] == 1


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            "sh -c 'redis-cli -a \"a\\\\\" ping' n=1",
            "sh -c 'redis-cli -a \"<redacted>\" ping' n=1",
        ),
        (
            'don\'t: redis-cli -a "a\\\\" ping n=1',
            'don\'t: redis-cli -a "<redacted>" ping n=1',
        ),
        ('redis-cli -a "a\\\\" ping "x" n=1', 'redis-cli -a "<redacted>" ping "x" n=1'),
    ],
)
def test_a_password_ending_in_a_backslash_still_ends_at_its_quote(line, expected):
    """A `'` that opens no string before the command (an apostrophe, a closing quote) adds no
    level, and with one that does, a quote nothing closes under that level is closed as before:
    the password `a\\` keeps the words after it."""
    assert _hidden_once(line) == expected


MID_WORD_QUOTES = [
    ('redis-cli -a x" s3cret" ping', "redis-cli -a <redacted> ping"),
    ("redis-cli -a 'a'\"'\"' s3cret' ping", "redis-cli -a '<redacted>' ping"),
    ("curl -u bob': s3cret' http://x", "curl -u bob':<redacted> http://x"),
]


@pytest.mark.parametrize(("command", "expected"), MID_WORD_QUOTES)
@pytest.mark.parametrize("form", ["plain", "json", "logfmt"])
def test_a_quote_in_the_middle_of_a_password_word_joins_what_it_quotes_to_the_word(
    command, expected, form
):
    """A quote anywhere in a shell word quotes on to its closer, as the shell reads it: only a
    quote that opened a string before the command closes that string after a word."""
    if form == "plain":
        line, kept = command, expected
    elif form == "json":
        line, kept = json.dumps({"cmd": command, "n": 1}), json.dumps({"cmd": expected, "n": 1})
    else:
        line, kept = _logfmt(command) + " n=1", _logfmt(expected) + " n=1"
    assert _hidden_once(line) == kept
    assert redact_command(command) == expected


@pytest.mark.parametrize("quote", ['x"', '"'])
@pytest.mark.parametrize("form", ["plain", "logfmt"])
def test_an_unclosed_quote_in_the_middle_of_a_password_hides_what_a_leading_one_does(quote, form):
    command = f"redis-cli -a {quote} s3cret ping"
    line = command if form == "plain" else _logfmt(command) + " n=1"
    expected = "redis-cli -a <redacted>" if form == "plain" else 'msg="redis-cli -a <redacted>'
    assert _hidden_once(line) == expected


@pytest.mark.parametrize(
    "line",
    [
        'msg="redis-cli -a pw1234" status=500',
        "cmd='redis-cli -a pw1234' status=500",
        'run ["sh", "-c", "redis-cli -a pw1234"] status=500',
        'msg="msg=\\"redis-cli -a pw1234\\" status=500"',
        'error: "curl -u bob:pw1234", status=500',
        'run "curl -u bob:pw1234". status=500',
    ],
)
def test_a_quote_that_opened_a_string_before_the_command_still_closes_it(line):
    out = _hidden_once(line)
    assert "pw1234" not in out and "status=500" in out


def test_a_closing_quote_put_back_in_a_json_string_keeps_the_json():
    line = json.dumps({"cmd": 'msg="redis-cli -a \\"pa ss\\"x ping"', "n": 1})
    out = _hidden_once(line)
    assert "pa ss" not in out
    assert json.loads(out)["n"] == 1


def test_a_password_hidden_to_the_end_counts_only_the_markers_left():
    line = "redis-cli -a 'pw mongo token=abc123 password=hunter22 x"
    out, count = redact_with_count(line)
    assert out == "redis-cli -a <redacted>"
    assert count == out.count("<redacted>") == 1
    assert redact_with_count(out) == (out, 0)


@pytest.mark.parametrize(
    "line",
    [
        "mongo -p'mongo' x",
        '{"cmd":"mongo -p\'mongo\' x","n":1}',
        "mongosh é -U -p' mysqladmin '' -U",
        "mongosh'-p'mysql -ps3c --eval",
        'mongo"-p""\t mysql',
        'mysql"-p""--user ;',
        "mongosh''-p' ' | ` \\  (",
        '{"cmd": "mongosh\'-p\'mysql -ps3c --eval", "n": 1}',
    ],
)
def test_a_password_running_into_a_client_name_is_hidden_once(line):
    out, count = redact_with_count(line)
    assert count >= 1
    assert redact_with_count(out) == (out, 0)


@pytest.mark.parametrize(
    "line",
    [
        '{"curl": "| --eval -u \'", "argv": ["|", "--eval", "-u", "\'"], "z": "mongo"}',
        '{"curl": "-u", "x": "bob:pw", "argv": ["-u", "\'"], "y": "\'"}',
    ],
)
def test_a_client_name_in_a_json_key_is_read_only_to_the_key_end(line):
    out = redact_text(line)
    assert json.loads(out).keys() == json.loads(line).keys()
    assert redact_with_count(out) == (out, 0)


def test_a_command_in_a_json_key_is_still_read():
    line = '{"curl -u a:pw1234": 1, "b": "redis-cli -a pw1234"}'
    out = _hidden_once(line)
    assert out == '{"curl -u a:<redacted>": 1, "b": "redis-cli -a <redacted>"}'
    json.loads(out)


def test_a_long_line_that_hiding_brings_under_the_cut_is_not_cut():
    line = "redis-cli -a " + "x" * 5000
    assert redact_with_count(line) == ("redis-cli -a <redacted>", 1)


def test_a_cut_line_counts_only_the_passwords_it_keeps():
    line = "a " * 2100 + "redis-cli -a pw1234 " * 200 + "b " * 2100
    out, count = redact_with_count(line)
    assert "pw1234" not in out
    assert count == out.count("<redacted>")
    kept = "a " * 2500 + "redis-cli -a pw1234 ping " + "b " * 300
    out, count = redact_with_count(kept)
    assert "pw1234" not in out
    assert count == out.count("<redacted>") == 1
    assert redact_with_count(out) == (out, 0)


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
        "redis-cli -a x/" * 260,
        "curl -u a:'mongo' " * 210,
        "mysqladmin -p'curl" * 210,
        'mongo started; cmd="redis-cli -a x" ' * 105,
        'curl {"cmd":"curl -u a:b"} ' * 140,
        json.dumps({"cmd": "redis-cli -a mongo " * 190}),
    ],
)
def test_redact_text_is_linear_on_lines_full_of_client_names(line):
    text = _lines_of(line)
    started = time.perf_counter()
    redact_text(text)
    assert time.perf_counter() - started < 1.0


@pytest.mark.parametrize(
    "piece", ["curl ", "redis-cli -a x/", "curl -u a:'mongo' ", "mysqladmin -p'curl", 'x="curl ']
)
def test_redact_text_is_linear_on_one_line_full_of_client_names(piece):
    text = piece * (1_000_000 // len(piece))
    started = time.perf_counter()
    redact_text(text)
    assert time.perf_counter() - started < 1.0


# Quoted passwords holding names, read again to the limit, and names in a JSON key
QUOTED_UNITS = [
    "mysqladmin -p'x mysql ",
    "redis-cli -a 'curl ",
    'redis-cli -a "curl ',
    "curl -u 'b:curl ",
    "mongo -p'mongo ",
    'mongo -p "mongo" ',
    "mysqladmin -p'mysql' ",
    "curl -u 'b:mysql' ",
    'redis-cli -a x/curl" ',
    "redis-cli -a --",
    'msg="redis-cli -a \\"x curl ',
    'curl -u \\"b:curl \\" ',
    "redis-cli -a \\'curl ",
    'redis-cli -a x" curl ',
    "redis-cli -a 'a'\"'\"' curl ",
    'curl -u bob": mysql ',
    'msg="redis-cli -a x\\" curl ',
]


@pytest.mark.parametrize("unit", QUOTED_UNITS)
@pytest.mark.parametrize("form", ["one", "lines", "json"])
def test_redact_text_is_linear_on_quoted_passwords_holding_names(unit, form):
    if form == "one":
        text = unit * (1_000_000 // len(unit))
    elif form == "lines":
        text = _lines_of(unit * (3900 // len(unit)))
    else:
        text = _lines_of(json.dumps({"cmd": unit * (3000 // len(unit)), "n": 1}))
    started = time.perf_counter()
    redact_text(text)
    assert time.perf_counter() - started < 1.0


@pytest.mark.parametrize("form", ["one", "lines"])
def test_redact_text_is_linear_on_json_keys_full_of_client_names(form):
    key = json.dumps({"curl -u a:'" * (330 if form == "lines" else 90_000): 1})
    text = key if form == "one" else _lines_of(key)
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


TWICE_ESCAPED_UNITS = [
    '\\\\\\"',
    "\\\\\\'",
    '\\\\"',
    '\\\\\\\\\\"',
    'redis-cli -a \\\\\\"',
    'redis-cli -a \\\\\\"x ',
    'msg=\\"redis-cli -a \\\\\\"x ',
    'mongo -p\\\\\\"',
    'curl -u \\\\\\"b:curl ',
    '\\\\\\" curl -u a:\\"',
    "curl \\\\",
    "\\u0022",
    "\\\\u0022 ",
    "redis-cli -a \\\\u0022x ",
    "\\u0027 curl -u a:\\u0022",
    'cmd=\'redis-cli -a "x\\\\" ',
    'msg=\'curl -u "b:\\\\\\\\" ',
    "'redis-cli -a \"",
]


@pytest.mark.parametrize("unit", TWICE_ESCAPED_UNITS)
@pytest.mark.parametrize("form", ["one", "after", "lines", "json"])
def test_redact_text_is_linear_on_quotes_escaped_twice(unit, form):
    if form == "one":
        text = unit * (1_000_000 // len(unit))
    elif form == "after":
        text = "redis-cli -a " + unit * (1_000_000 // len(unit))
    elif form == "lines":
        text = _lines_of(unit * (3900 // len(unit)))
    else:
        text = _lines_of(json.dumps({"m": "redis-cli -a " + unit * (1500 // len(unit))}))
    started = time.perf_counter()
    redact_text(text)
    assert time.perf_counter() - started < 1.0


def _widening_quotes(length: int) -> str:
    """Escaped quotes of every width, none closed: `\\" \\\\" \\\\\\" …`, each a kind of its own."""
    units, width = [], 1
    while sum(map(len, units)) < length:
        units.append("\\" * width + '" ')
        width += 1
    return "".join(units)[:length]


@pytest.mark.parametrize("form", ["one", "lines", "json"])
def test_redact_text_is_linear_on_escaped_quotes_of_every_width(form):
    if form == "one":
        text = "redis-cli -a " + _widening_quotes(1_000_000)
    elif form == "lines":
        text = _lines_of("redis-cli -a " + _widening_quotes(3900))
    else:
        text = _lines_of(json.dumps({"m": "redis-cli -a " + _widening_quotes(1800)}))
    started = time.perf_counter()
    redact_text(text)
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
