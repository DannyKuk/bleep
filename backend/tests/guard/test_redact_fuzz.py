"""Generated client commands, quoted the ways a shell quotes them and written into a log the ways
logs write them (plain, JSON, logfmt, logfmt or JSON inside logfmt, Python's repr): the password
never shows, what follows it stays, a JSON record stays JSON and a second pass changes nothing.

Seeded, so a failure names the seed and the form and reproduces."""

import json
import random
import re
import shlex
from collections.abc import Callable

import pytest

from bleep.guard.redact import redact_with_count

LINES_PER_FORM = 1500
NAMES = ["curl", "mysql", "mongo", "redis-cli", "mongosh", "mysqladmin"]
PLAIN_PIECES = [*NAMES, "ab", "-p", "-a", "x/y", "é", "😀", ",", ";", "'"]
PIECES = [*PLAIN_PIECES, "\\", "\\\\", '"']
PREFIXES = ["", "+ ", "curl -s x && ", "mongo started; ", "echo ok; ", "INFO curl check ", "sh -c "]
KEEP = "KEEPME"
CORE = re.compile(r"Zq[0-9]{3}Xw")
STRING_END = re.compile(rf"[\s,;:.)}}\]]|{'|'.join(NAMES)}")
SHELL_SPECIAL = set(" '\"\\;`&|(")


def _secret(rng: random.Random, pieces: list[str]) -> tuple[str, str]:
    """A password and the core that must never show: client names, flags, blanks, quotes,
    backslashes and punctuation around it, so it starts with a blank or a comma as often as
    with a letter."""
    core = f"Zq{rng.randint(100, 999)}Xw"
    parts = [rng.choice(["", " "]) + core]
    for _ in range(rng.randint(0, 3)):
        parts.append(rng.choice(["", " ", "  "]) + rng.choice(pieces))
    rng.shuffle(parts)
    return "".join(parts), core


def _double_quoted(value: str) -> str:
    """`value` as a shell writes it inside double quotes, its `\\`, `"`, `$` and `` ` `` escaped."""
    return '"' + "".join("\\" + c if c in '\\"$`' else c for c in value) + '"'


def _quoted(rng: random.Random, value: str, split: bool) -> str:
    """`value` quoted as a shell would, or, when `split`, also as a double-quoted part and a
    single-quoted one (`"pw\\\\"'" s3cret'`), cut outside the core. One holding `'` goes in double
    quotes, as `shlex.quote` would put a quote in the middle of the word (`'a'"'"'b'`), and the
    single-quoted part never starts with a blank, punctuation or a client's name: a quote there,
    after a word and before a blank or the next command, is read as the end of a string around
    the command (docs/design/guard.md)."""
    draw = rng.random()
    core = CORE.search(value)
    if split and core and draw < 0.2 and "'" not in value:
        cuts = [*range(1, core.start() + 1), *range(core.end(), len(value))]
        cuts = [cut for cut in cuts if not STRING_END.match(value, cut)]
        if cuts:
            cut = rng.choice(cuts)
            return _double_quoted(value[:cut]) + f"'{value[cut:]}'"
    if "'" in value or 0.4 <= draw < 0.7:
        return _double_quoted(value)
    if draw < 0.4:
        return shlex.quote(value)
    if not set(value) & SHELL_SPECIAL:
        return value
    return shlex.quote(value)


def _command(rng: random.Random, plain: bool) -> tuple[str, str]:
    """A client's command with a generated password; a `plain` one holds no backslash or `"`
    and is quoted in one part."""
    value, core = _secret(rng, PLAIN_PIECES if plain else PIECES)
    quoted = _quoted(rng, value, not plain)
    user = _quoted(rng, "bob:" + value, not plain)
    commands = [
        f"redis-cli -h db -a {quoted} ping",
        f"redis-cli --pass {quoted} ping",
        f"mongosh -u root -p {quoted} --eval 1",
        f"curl -sS -u {user} http://x/h",
        f"curl -fsSu {user} http://x/h",
        f"curl -u{user} http://x/h",
        f"mysqladmin -uroot -p{quoted} ping",
        f"mongo -p{quoted} --quiet",
    ]
    return rng.choice(commands), core


def _logfmt(text: str, quote: str = '"') -> str:
    return f"msg={quote}" + text.replace("\\", "\\\\").replace(quote, "\\" + quote) + quote


# Each form writes a line holding the command (and the bare command, for the argv list)
FORMS: dict[str, Callable[[str, str, random.Random], str]] = {
    "plain": lambda line, _, __: line,
    "json": lambda line, _, rng: json.dumps(
        {"cmd": line, "k": KEEP}, ensure_ascii=rng.random() < 0.5
    ),
    "json in msg": lambda line, _, __: "msg=" + json.dumps(line) + f" status=500 {KEEP}",
    "code span": lambda line, _, __: f"run `{line}` first. {KEEP}",
    "argv": lambda _, command, __: json.dumps({"argv": shlex.split(command), "k": KEEP}),
    "sh -c": lambda line, _, __: json.dumps(["sh", "-c", line, KEEP]),
    "logfmt": lambda line, _, __: _logfmt(line) + f" status=500 {KEEP}",
    "logfmt single": lambda line, _, __: _logfmt(line, "'") + f" status=500 {KEEP}",
    "logfmt at end": lambda line, _, __: _logfmt(line),
    "logfmt twice": lambda line, _, __: _logfmt(_logfmt(line)) + f" {KEEP}",
    "json in logfmt": lambda line, _, __: _logfmt(json.dumps({"cmd": line})) + f" {KEEP}",
    "logfmt in json": lambda line, _, __: json.dumps({"log": _logfmt(line), "k": KEEP}),
    "json in json": lambda line, _, __: json.dumps({"log": json.dumps({"cmd": line}), "k": KEEP}),
    "logfmt twice in json": lambda line, _, __: json.dumps(
        {"log": _logfmt(_logfmt(line)), "k": KEEP}
    ),
    "repr": lambda line, _, __: f"cmd={line!r} {KEEP}",
    "two values": lambda line, _, __: (
        _logfmt(line) + " other=" + _logfmt(f'redis-cli -a "x y" {KEEP}')
    ),
}
# Forms that quote with `'` and leave `"` as it is: the shell's double quote stays a plain quote
# whose escaped backslashes they double, which the redaction does not read (docs/design/guard.md)
DOUBLE_QUOTE_LEFT_PLAIN = {"logfmt single", "repr"}
JSON_FORMS = {"json", "argv", "sh -c", "logfmt in json", "json in json", "logfmt twice in json"}


def _problems(form: str, seed: int) -> list[tuple[str, str, str]]:
    rng = random.Random(f"{form}/{seed}")
    problems = []
    for _ in range(LINES_PER_FORM):
        command, core = _command(rng, form in DOUBLE_QUOTE_LEFT_PLAIN)
        line = FORMS[form](rng.choice(PREFIXES) + command, command, rng)
        out, count = redact_with_count(line)
        if core in out:
            problems.append(("leak", line, out))
        if KEEP in line and KEEP not in out:
            problems.append(("hides too much", line, out))
        if form in JSON_FORMS:
            try:
                json.loads(out)
            except json.JSONDecodeError:
                problems.append(("not JSON", line, out))
        if redact_with_count(out) != (out, 0) or count < 0:
            problems.append(("second pass", line, out))
    return problems


@pytest.mark.parametrize("form", FORMS)
def test_generated_client_commands_never_show_their_password(form):
    assert _problems(form, seed=1)[:5] == []
