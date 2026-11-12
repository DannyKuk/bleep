"""Keep secrets away from the model, the logs and the database.

Redaction is best-effort defence in depth. What it must never do is take unbounded time, so the
guard first cuts the input (see `guard.pipeline`), `redact_with_count` cuts every line over 4,000
characters, and each pattern below is written to run in linear time on what is left.
"""

import bisect
import ipaddress
import itertools
import re
import string
from collections.abc import Callable
from functools import lru_cache

from bleep.guard.sanitize import (
    CONTROL_CHARS,
    MAX_LEAD,
    MAX_LINE,
    PRESENTATION_SELECTORS,
    VALUE_END,
    cut_points,
    join_cut,
    normalise_newlines,
)
from bleep.schemas.graph import PlanStep, Proposal

REDACTED = "<redacted>"


def redact_env(env: list[str]) -> list[str]:
    """`KEY=value` becomes `KEY=<redacted>`, except that a URL keeps its scheme, host and port
    (`KEY=postgresql://<redacted>@db:5432/<redacted>`); a bare `KEY` stays as it is."""
    return [_redact_env_entry(item) if "=" in item else item for item in env]


def _redact_env_entry(item: str) -> str:
    """The URL's place is kept only when `redact_text` would keep the whole entry as it is, so a
    host that looks like a token, or a value under a secret key (`REDIS_PASSWORD=redis://…`), is
    hidden here as well and not only by the pipeline later."""
    key, _, value = item.partition("=")
    place = _url_place(value)
    entry = f"{key}={place}"
    return entry if place is not None and redact_text(entry) == entry else f"{key}={REDACTED}"


def secret_env_values(env: list[str]) -> list[str]:
    """The values of the entries whose `KEY=value` the redaction would change (a secret key such
    as `REDIS_PASSWORD`), longest first. Compose copies such a value into a health-check command,
    where `hide_values` finds the same text; `PORT=8000` is no secret and is not in the list."""
    values = {
        item.partition("=")[2]
        for item in env
        if "=" in item and item.partition("=")[2] and redact_text(item) != item
    }
    return sorted(values, key=len, reverse=True)


LONG_VALUE = 8  # from this length a value is hidden wherever it shows, inside a word too


def hide_values(text: str, values: list[str]) -> str:
    """Every one of `values` in `text` hidden, in one pass so a marker already placed is never
    rewritten. A value of 8 or more characters is hidden wherever it appears (`-ps3cretpassw0rd`:
    inside a word it is still that secret). A shorter one only as a whole token, not inside a
    word, so `SECRET_KEY=e` does not turn `timed out` into `tim<redacted>d out`. A value that is
    also a plain word in the text (a password `shop` for the user `shop`) is hidden there too: it
    is the password."""
    alternatives = []
    for value in sorted({v for v in values if v}, key=len, reverse=True):
        escaped = re.escape(value)
        if len(value) < LONG_VALUE:
            escaped = (r"(?<!\w)" if re.match(r"\w", value[0]) else "") + escaped
            escaped += r"(?!\w)" if re.match(r"\w", value[-1]) else ""
        alternatives.append(escaped)
    if not alternatives:
        return text
    pattern = re.compile("|".join([re.escape(REDACTED), *alternatives]))
    return pattern.sub(lambda m: REDACTED, text)


def redact_command(text: str) -> str:
    """`redact_text` for a health-check command, then the password a database or HTTP client
    takes on its command line (`hide_client_passwords`). Only for a line known to be a command:
    free text such as a log line is never read this way."""
    return hide_client_passwords(redact_text(text))


_CLIENT_NAME = (
    r"redis-cli|mysql(?:admin|dump|check|import|show)?|mariadb(?:-[a-z]+)?|mongo(?:sh|s)?|curl"
)
_CLIENT_COMMAND = re.compile(rf"(?<![A-Za-z0-9_.-])({_CLIENT_NAME})(?![A-Za-z0-9_-])[^\n]*")
_WORD_NAME = re.compile(rf"(?:.*[/;&|(])?({_CLIENT_NAME})")  # curl, /usr/bin/curl, &&curl
_SEPARATE_PASSWORD = {
    "redis-cli": {"-a", "--pass"},
    "mongosh": {"-p", "--password"},
    "mongo": {"-p", "--password"},
    "mongos": {"-p", "--password"},
}
_USER_FLAGS = {"-u", "--user", "--proxy-user"}
_PLAIN_RUN = re.compile(r"[^\s\"']+")
_QUOTE = re.compile(r"[\"']")
_DOUBLE_QUOTED_END = re.compile(r"(?:[^\"\\]|\\.)*+\"", re.DOTALL)


def _quote_end(text: str, at: int, no_closer: set[str]) -> int:
    """Where the quoted string opening at `at` ends, or -1. A quote nothing closes is looked for
    once: the quote kind goes into `no_closer`, so a line of such quotes costs one scan, not one
    each."""
    quote = text[at]
    if quote in no_closer:
        return -1
    if quote == "'":
        end = text.find("'", at + 1)
        end = end + 1 if end >= 0 else -1
    else:
        found = _DOUBLE_QUOTED_END.match(text, at + 1)
        end = found.end() if found else -1
    if end < 0:
        no_closer.add(quote)
    return end


def _shell_words(text: str, start: int) -> tuple[list[tuple[int, int]], int | None]:
    """The words of a shell command from `start`, and where an unclosed quote opens, if one does.

    A word runs to whitespace, quoted parts included (`-p'pa ss'`, `pa,ss`). A quote nothing
    closes after whitespace would swallow the rest of the line in a shell: the caller hides it
    from there. One attached to a word (the closing quote of a JSON string around the command)
    only ends that word.
    """
    words, no_closer, at, end = [], set[str](), start, len(text)
    while at < end:
        if text[at].isspace():
            at += 1
            continue
        first = at
        while at < end and not text[at].isspace():
            run = _PLAIN_RUN.match(text, at)
            if run:
                at = run.end()
                continue
            closed = _quote_end(text, at, no_closer)
            if closed >= 0:
                at = closed
            elif at > first:
                break
            elif text[first - 1].isspace():
                return words, first
            else:
                at += 1
                first = at
        if at > first:
            words.append((first, at))
    return words, None


def _json_items(text: str, start: int) -> list[tuple[int, int]]:
    """The quoted strings of a JSON list from `start`, and nothing between them."""
    items, no_closer, at = [], set[str](), start
    while found := _QUOTE.search(text, at):
        end = _quote_end(text, found.start(), no_closer)
        if end < 0:
            at = found.end()
            continue
        items.append((found.start(), end))
        at = end
    return items


def _hide_after(word: str, keep: int) -> str:
    """The word with all but its first `keep` characters hidden, its quotes kept."""
    quote = word[0] if word[0] in "\"'" else ""
    inner = word[1:-1] if quote and len(word) > 1 and word.endswith(quote) else word.lstrip(quote)
    if not inner[keep:] or inner[keep:].startswith(REDACTED):
        return word
    return f"{quote}{inner[:keep]}{REDACTED}{quote}"


def _hide_user_password(word: str, skip: int) -> str:
    """`user:password` with the password hidden; a word with no colon names a user only."""
    colon = word.strip("\"'")[skip:].find(":")
    return word if colon < 0 else _hide_after(word, skip + colon + 1)


def _hide_in_command(match: re.Match[str]) -> str:
    """One command line from a client's name on. Reads `-a`, `--pass`, `-p` and `--password` for
    `redis-cli` and the `mongo` family, `-u` and `--user` for `curl`, and the attached `-ppw` of
    the MySQL clients, as shell words or as the items of a JSON list. A later client's name in
    the same line (`curl … && redis-cli -a pw`) switches the rules."""
    text, name = match.group(0), match.group(1)
    after_name = match.end(1) - match.start()
    if text[after_name : after_name + 1] in ("'", '"'):  # the name was a JSON item: close it
        spans, unclosed = _json_items(text, after_name + 1), None
    else:
        spans, unclosed = _shell_words(text, after_name)
    words = [text[a:b] for a, b in spans]
    changes: dict[int, str] = {}
    for index, word in enumerate(words):
        if index in changes:
            continue
        bare = word.strip("\"'")
        if later := _WORD_NAME.fullmatch(bare):
            name = later.group(1)
            continue
        following = index + 1 if index + 1 < len(words) else None
        if following is not None and words[following].strip("\"'").startswith("-"):
            following = None
        if name.startswith(("mysql", "mariadb")):
            if bare.startswith("-p") and len(bare) > 2 and bare[2] != "=":
                changes[index] = _hide_after(word, 2)
        elif name == "curl":
            if bare in _USER_FLAGS and following is not None:
                changes[following] = _hide_user_password(words[following], 0)
            elif bare.startswith("-u") and len(bare) > 2 and not bare.startswith("--"):
                changes[index] = _hide_user_password(word, 2)
        elif bare in _SEPARATE_PASSWORD.get(name, ()) and following is not None:
            changes[following] = _hide_after(words[following], 0)
    out, kept = [], 0
    for index in sorted(changes):
        a, b = spans[index]
        if changes[index] != words[index]:
            out.append(text[kept:a] + changes[index])
            kept = b
    if unclosed is None:
        return "".join(out) + text[kept:]
    return "".join(out) + text[kept:unclosed] + REDACTED


def hide_client_passwords(text: str) -> str:
    """The password a database or HTTP client takes on its command line: `redis-cli -a pw` and
    `--pass pw`, `mongosh -p pw` and `--password pw`, `curl -u user:pw`, `mysqladmin -ppw`
    (quoted or not). A shell word is hidden whole, up to whitespace, whatever its quotes and
    commas. It looks only after a client's name, because `-a` and `-p` mean other things
    elsewhere; and only for a line known to be a command (`redact_command`), not for the global
    `redact_text`, which every log line goes through."""
    return _CLIENT_COMMAND.sub(_hide_in_command, text)


_URL_SCHEME = re.compile(r"([A-Za-z][A-Za-z0-9+.-]{0,31})://")
# Schemes that name a network service, whose host is where it runs. Notification URLs
# (`pushbullet://<token>`, `pover://<user>@<token>`) put a token where the host goes.
_SERVICE_SCHEMES = frozenset({
    "http", "https", "ws", "wss", "postgres", "postgresql", "mysql", "mariadb", "redis", "rediss",
    "mongodb", "amqp", "amqps", "nats", "kafka", "mqtt", "mqtts", "smtp", "smtps", "ldap", "ldaps",
})  # fmt: skip
_DRIVER = re.compile(r"[a-z0-9]{1,24}")  # postgresql+psycopg, mongodb+srv
_TOKEN_SEGMENT = re.compile(r"(?=[A-Za-z0-9]*[A-Za-z])(?=[A-Za-z0-9]*[0-9])[A-Za-z0-9]{16,}")
_LETTERS_SEGMENT = re.compile(r"[A-Za-z]{16,}")
# A random 20-letter token switches case about 10 times; a CamelCase service name such as
# `PaymentGatewayService` switches once per word boundary, 5 times.
MIN_CASE_SWITCHES = 6
_UUID = re.compile(r"[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}")
_DNS_LABEL = r"[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?"
_HOST_NAME = re.compile(rf"{_DNS_LABEL}(?:\.{_DNS_LABEL})*")
_IPV6 = re.compile(r"\[([0-9A-Fa-f:.]{2,45})\]")
_PORT = re.compile(r"[0-9]{1,5}")
_PRINTABLE = re.compile(r"[!-~]*")
MAX_HOST = 253


def _url_place(value: str) -> str | None:
    """`scheme://host:port` of a URL with every other part present shown as `<redacted>`, or None
    when the value is not plainly such a URL.

    Clients disagree on where the userinfo ends (libpq stops at the first `/`, urllib takes
    the last `@`, browsers read `\\` as `/`), and a wrong guess would show part of a
    password as the host. So any value the readings could disagree on is refused: anything
    outside printable ASCII, a backslash, more than one `@` or an `@` after the authority, and
    a host or port outside a strict allowlist. So is a scheme that is not a network service and
    a host that looks like a token.
    """
    scheme = _URL_SCHEME.match(value)
    if scheme is None or "\\" in value or not _PRINTABLE.fullmatch(value):
        return None
    if not _is_service_scheme(scheme.group(1)):
        return None
    rest = value[scheme.end() :]
    end = min((i for i in map(rest.find, "/?#") if i >= 0), default=len(rest))
    authority, tail = rest[:end], rest[end:]
    if "@" in tail or authority.count("@") > 1:
        return None
    _, at, host_port = authority.rpartition("@")
    place = _host_port(host_port)
    if place is not None and _looks_like_token(place):
        return None
    if place is None:
        return None
    return f"{scheme.group(0)}{REDACTED + '@' if at else ''}{place}{_hidden_tail(tail)}"


def _is_service_scheme(scheme: str) -> bool:
    base, plus, driver = scheme.lower().partition("+")
    return base in _SERVICE_SCHEMES and (not plus or bool(_DRIVER.fullmatch(driver)))


def _looks_like_token(place: str) -> bool:
    """A UUID, or a host segment of 16 or more characters that mixes letters with digits or is
    letters switching case at least 6 times, is read as a key (a Lambda function URL id,
    `sk_live_…`, `glpat-…`); a 12-hex container id is shorter, and a CamelCase service name
    switches case less often."""
    if place.startswith("["):
        return False
    host = place.partition(":")[0]
    return bool(_UUID.search(host)) or any(
        _is_token_segment(segment) for segment in re.split(r"[._-]", host)
    )


def _is_token_segment(segment: str) -> bool:
    if _TOKEN_SEGMENT.fullmatch(segment):
        return True
    if not _LETTERS_SEGMENT.fullmatch(segment):
        return False
    switches = sum(a.isupper() != b.isupper() for a, b in itertools.pairwise(segment))
    return switches >= MIN_CASE_SWITCHES


def _host_port(text: str) -> str | None:
    """`host` or `host:port` when the host is a DNS name, an IPv4 address or a bracketed IPv6
    address and the port is a number up to 65535; otherwise None."""
    ipv6 = _IPV6.match(text)
    if ipv6 is not None:
        host, after = ipv6.group(0), text[ipv6.end() :]
        if after and not after.startswith(":"):
            return None
        port = after[1:] if after else None
        try:
            ipaddress.IPv6Address(ipv6.group(1))
        except ValueError:
            return None
    else:
        host, colon, port = text.partition(":")
        if not colon:
            port = None
        if len(host) > MAX_HOST or not _HOST_NAME.fullmatch(host):
            return None
    if port is None:
        return host
    if not _PORT.fullmatch(port) or int(port) > 65535:
        return None
    return f"{host}:{port}"


def _hidden_tail(tail: str) -> str:
    """Path, query and fragment, each as its delimiter plus `<redacted>` when it holds more."""
    before_fragment, hash_sign, fragment = tail.partition("#")
    path, question_mark, query = before_fragment.partition("?")
    parts = ((path[:1], path[1:]), (question_mark, query), (hash_sign, fragment))
    return "".join(delimiter + (REDACTED if text else "") for delimiter, text in parts)


_FLAGS = re.IGNORECASE
# A cheap test that a key name may be secret, before `_is_secret_key` judges it.
_SECRET_HINT = r"(?:pass|pw|secret|token|credential|auth|key|salt|jwt|hmac|pat|dsn|sk)"
_SCHEME = r"(?:bearer|basic|token|digest)"

# --- key names: split into words and judged in Python ----------------------------------------
WORDS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")  # SECRET_KEY, secretKey, APIKey
_SECRET_WORDS = {
    "password", "passwd", "pass", "pwd", "passphrase", "secret", "secrets", "token", "credential",
    "credentials", "auth", "apikey", "pw", "secretkey", "privatekey", "accesskey", "accesstoken",
    "authkey", "salt", "jwt", "hmac", "pat", "pgpass",
}  # fmt: skip
_SECRET_SUFFIXES = ("password", "passwd", "apikey", "secret", "authkey")  # PGPASSWORD, TS_AUTHKEY
# Words that keep their meaning with a trailing "s" (PASSWORDS, API_KEYS, access_tokens).
_PLURALS = {
    "password", "passwd", "pwd", "passphrase", "credential", "apikey", "key", "authkey",
    "secretkey", "privatekey", "accesskey", "token",
}  # fmt: skip
# `<x>_key` and `xKey` are secrets (stripe_key, aws_key, googleKey) unless x says the key is an
# identifier: a database, cache, map or storage key.
_PLAIN_KEY_PREFIXES = {
    "cache", "primary", "foreign", "sort", "partition", "routing", "idempotency", "hash", "row",
    "map", "dict", "lookup", "unique", "composite", "shard", "object", "s3", "bucket", "cursor",
    "page", "version", "public", "index", "lock", "queue", "job", "group", "order", "message",
    "event", "metric", "label", "tag", "field", "column", "config", "setting", "translation",
    "dedup", "dedupe", "limit", "ratelimit", "request", "trace", "correlation", "natural",
    "surrogate", "compound", "parent", "child", "ref", "cluster", "node", "redis", "kafka", "file",
    "registry", "record", "entity", "item", "list", "table", "sequence", "id",
}  # fmt: skip
# Pagination and partition keys, readable only as the whole name (`exclusive_start_key`,
# `LastEvaluatedKey`): `stripe_last_key`, `next_key` and `tenant_key` can hold a real key.
_PLAIN_KEY_NAMES = {
    "last_key", "start_key", "end_key", "exclusive_start_key", "last_evaluated_key", "sharding_key",
}  # fmt: skip
# After `tokens`, a count: max_tokens, prompt_tokens. Other `*_tokens` are secrets.
_TOKEN_COUNTS = {
    "max", "min", "prompt", "completion", "total", "input", "output", "num", "cached", "reasoning",
    "context", "audio", "remaining", "used",
}  # fmt: skip
# KEY, APP_KEY, STRIPE_KEY, PUSHER_APP_KEY, STRIPE_SK; not cache_key, not a Dynamo `sk` in a log
_ENV_KEY = re.compile(r"KEY|[A-Z0-9_]+_(?:KEYS?|SK)")
# Two lists of place words, on purpose. In a log, which goes to the model, only these keep a value
# after a secret word (`token_url`), since `PASSWORD_HOST=hunter2` is a password as often as a
# host. In a set_env approval the person must see where a value points, so any of
# `_APPROVAL_PLACES` shows it (`AUTH0_DOMAIN`), value-level redaction aside.
_LOG_PLACES = frozenset({"url", "uri", "endpoint"})
_APPROVAL_PLACES = _LOG_PLACES | {
    "host", "domain", "server", "issuer", "proxy", "origin", "callback", "redirect", "addr",
    "address",
}  # fmt: skip
# A word AFTER the last secret word that marks a setting about the secret: token_count,
# password_reset, auth_method, token_url. A word before it changes nothing (reset_password_token
# is a token).
_NOT_A_SECRET = _LOG_PLACES | {
    "count", "reset", "type", "name", "file", "path", "length", "expires", "expiry", "expiration",
    "ttl", "header", "prefix", "limit", "max", "min", "mode", "size", "bytes", "format",
    "algorithm", "alg", "kind", "provider", "status", "required", "enabled", "disabled", "policy",
    "dir", "folder", "method", "mechanism", "backend", "source", "audience", "scheme", "stdin",
    "used", "hashes",
}  # fmt: skip
# Values that are never secrets, compared case-insensitively against the whole value.
_PLAIN_VALUES = {
    "true", "false", "yes", "no", "on", "off", "none", "null", "nil", "empty", "unset", "required",
    "enabled", "disabled", "expired", "invalid", "missing", "ok", "failed", "***", "<redacted>",
}  # fmt: skip
# `${VAR}`, `${secrets.db}`, `${env:VAR}` (a known source before the colon), an absolute file path
# (`${file:/run/secrets/db}`) and `$UPPER_VAR`. A default (`${VAR:-x}`,
# `${VAR:=x}`, `${VAR-x}`, Spring's `${VAR:x}`) holds a literal value and is not a reference.
_REFERENCE = re.compile(
    r"\$\{(?:(?:env|file|secrets|secret|vault|ssm|sys|aws):)?[A-Za-z_][A-Za-z0-9_.]*\}"
    r"|\$\{file:/[A-Za-z0-9_./-]{1,255}\}"
    r"|\$[A-Z_][A-Z0-9_]*"
)
_SMALL_INT = re.compile(r"[0-9]{1,6}")
_LOOKS_LIKE_PAIR = re.compile(r"[A-Za-z_][\w.-]*=[^=\s]")
_PAIR_FOLLOWS = re.compile(r"[^\S\n]+[A-Za-z_][\w.-]*=\S")
_NOT_SET = re.compile(r"not set\b", re.IGNORECASE)
# YAML's block indicator: `|` or `>`, an indentation digit and a chomping sign in either order,
# so `|4242` is a value, not a header (#417)
_BLOCK_INDICATOR = r"[|>](?:[1-9][-+]?|[-+][1-9]?)?"
_BLOCK_HEADER = re.compile(_BLOCK_INDICATOR)


@lru_cache(maxsize=4096)
def _is_secret_key(key: str) -> bool:
    if key in ("PWD", "OLDPWD"):  # the shell's working directory
        return False
    if _ENV_KEY.fullmatch(key):
        return True
    raw = [w.lower() for w in WORDS.findall(key)]
    if "_".join(raw) in _PLAIN_KEY_NAMES:
        return False
    words = [_singular(w) for w in raw]
    last = -1
    for index, word in enumerate(words):
        before = words[index - 1] if index > 0 else None
        if before and before.isdigit() and index > 1:
            before = words[index - 2] + before  # s3_key, i18n_key
        if raw[index] == "tokens" and before in _TOKEN_COUNTS:
            continue
        if (
            word in _SECRET_WORDS
            or word.endswith(_SECRET_SUFFIXES)
            or (len(word) > 5 and word.endswith("token"))
            or (word == "key" and before is not None and before not in _PLAIN_KEY_PREFIXES)
        ):
            last = index
    if last < 0:
        return False
    after = words[last + 1 :]
    if any(w in _NOT_A_SECRET for w in after):
        return False
    return not ("id" in after and words[last] in ("token", "session"))


def _singular(word: str) -> str:
    stem = word[:-1]
    if word.endswith("s") and (stem in _PLURALS or stem.endswith((*_SECRET_SUFFIXES, "token"))):
        return stem
    return word


def _is_plain(value: str) -> bool:
    if _REFERENCE.fullmatch(value.rstrip(",;")):
        return True
    value = value.rstrip(",;})]\"'").lower()  # `<redacted>"` when a list ended inside quotes
    return value in _PLAIN_VALUES or _SMALL_INT.fullmatch(value) is not None


# A part of a setting's path written as a key: `ClientSecret`, `clientSecret`, `client_secret`,
# `CLIENT_SECRET`, and dotted (`Spring.Datasource`). A plain word (`secret`) or one with digits
# (`Secret123`) may be a value.
_KEY_FORM = (
    r"(?:[A-Z][a-z]+(?:[A-Z][a-z]*)*|[a-z]+(?:[A-Z][a-z]*)+|[a-z]+(?:_[a-z]+)+|[A-Z]+(?:_[A-Z]+)+)"
)
_SETTING_PATH = re.compile(rf"{_KEY_FORM}(?:\.{_KEY_FORM})*(?::{_KEY_FORM}(?:\.{_KEY_FORM})*)+")
_NAME_ATTRIBUTE = re.compile(
    r"(?<![A-Za-z0-9_.:-])(?:key|name)[ \t]*=[ \t]*([\"'])([A-Za-z0-9_.:-]{1,96})\1", _FLAGS
)
_NAME_ATTRIBUTE_OPEN = re.compile(r"(?<![A-Za-z0-9_.:-])(?:key|name)[ \t]*=[ \t]*[\"']\Z", _FLAGS)


def _names_a_setting(match: re.Match[str]) -> bool:
    """Whether the pair is a setting's name in a `key` or `name` attribute (`key="Jwt:Secret"`,
    `name="Auth:ClientSecret"`): a path of parts written as keys whose last part names a secret.
    There `:` divides the path, and `Secret` is no value. `key="token:abc123"` and
    `key="password:Secret123"` stay pairs."""
    attribute = _NAME_ATTRIBUTE.fullmatch(match.group(0))
    if attribute:  # the pair is the attribute itself: `key` and the quoted path
        path = attribute.group(2)
    else:
        # the pair starts inside the attribute's quotes (`name="Auth:ClientSecret"`, where `Auth`
        # is the key `_KEY_VALUE` found): the attribute must open right before it, and the pair
        # must end with the closing quote
        start, value = match.start(), match.group(3)
        quote = match.string[start - 1 : start]
        if not quote or not value.endswith(quote) or quote not in "\"'":
            return False
        if not _NAME_ATTRIBUTE_OPEN.search(match.string, max(0, start - 40), start):
            return False
        path = match.group(0)[:-1]
    if len(path) > 96 or not _SETTING_PATH.fullmatch(path):
        return False
    return _is_secret_key(path.rpartition(":")[2])


# One JSON token after blanks: a string (its body, then its closing quote, missing when the line
# ends first), a number or literal, or a bracket, comma or colon. Only the first `-` of a number
# is optional, so a date (`2024-01-01`) reads as numbers in a row, which JSON never has.
_JSON_TOKEN = re.compile(
    r"[ \t]*+(?:(\"(?:[^\"\\\n]|\\.)*+)(\")?|-?[0-9]++(?:\.[0-9]++)?(?:[eE][+-]?[0-9]++)?"
    r"|true|false|null|([{}\[\],:]))"
)
# JSON tokens in a row, the last string maybe left open, with `<redacted>` as a token too
_JSON_RUN = re.compile(
    r"(?:\"(?:[^\"\\\n]|\\.)*+\"|-?[0-9]++(?:\.[0-9]++)?(?:[eE][+-]?[0-9]++)?|true|false|null"
    r"|<redacted>|[{}\[\],:])*+(?:\"(?:[^\"\\\n]|\\.)*+)?"
)
_BLANKS = re.compile(r"[ \t]*+")
# The note `join_cut` puts between a cut line's head and tail, each on a line of its own
_CUT_NOTE_BEFORE = re.compile(r"\[… [0-9]+ chars cut\]\n\Z")
_CUT_NOTE_AFTER = re.compile(rf"\n[^\n]{{0,{MAX_LEAD}}}?\[… [0-9]+ chars cut\](?:\n|\Z)")
_OPENER_OF = {"}": "{", "]": "["}


@lru_cache(maxsize=16)
def _json_strings(text: str, line_start: int) -> tuple[tuple[int, ...], tuple[int, ...], int]:
    """The line of `text` at `line_start` read as JSON: where each string that is a value (not a
    key) opens and closes, and where its JSON ends; nothing when the line is not JSON from where
    its JSON starts to its end or the JSON's end. The JSON starts at the line's first `{` (or `[`
    when it has none) with no `"` before it, after a log prefix, or at the line's start when that
    is a key, as on a line of pretty-printed JSON. Read once per line, so each pair on it costs a
    bisection. The head and tail kept of a line over 4,000 characters, on lines of their own
    around the cut note, are no JSON (`_cut_line`)."""
    end = text.find("\n", line_start)
    end = len(text) if end < 0 else end
    if _CUT_NOTE_BEFORE.search(text, max(0, line_start - 40), line_start) or _CUT_NOTE_AFTER.match(
        text, end
    ):
        return (), (), line_start
    pos = _BLANKS.match(text, line_start, end).end()
    member_line = text.startswith('"', pos)  # one member of an object, as pretty-printed
    if member_line:
        stack, expect = ["{"], "key"
    else:
        pos = text.find("{", line_start, end)
        pos = text.find("[", line_start, end) if pos < 0 else pos
        if pos < 0 or '"' in text[line_start:pos]:
            return (), (), line_start
        stack, expect = [], "value"
    opens: list[int] = []
    closes: list[int] = []
    fresh = False  # right after `{` or `[`, where the bracket may close at once
    keys = 0  # of the outermost object
    while pos < end and expect != "done":
        token = _JSON_TOKEN.match(text, pos, end)
        if token is None:
            break
        body, mark = token.group(1), token.group(3)
        if body is not None and not token.group(2):
            break  # a string the line ends inside: what comes after it is unknown
        outermost = len(stack) == 1
        if body is not None and expect == "key" and not (member_line and outermost and keys):
            expect = "colon"
            keys += outermost
        elif body is not None and expect == "value":
            opens.append(token.start(1))
            closes.append(token.start(2))
            expect = "comma"
        elif body is None and mark is None and expect == "value":
            expect = "comma"
        elif mark in ("{", "[") and expect == "value":
            stack.append(mark)
            expect = "key" if mark == "{" else "value"
        elif mark in _OPENER_OF and stack[-1] == _OPENER_OF[mark] and (fresh or expect == "comma"):
            stack.pop()
            expect = "comma" if stack else "done"
        elif mark == ":" and expect == "colon":
            expect = "value"
        elif mark == "," and expect == "comma":
            expect = "key" if stack[-1] == "{" else "value"
        else:
            break
        fresh = mark in ("{", "[")
        pos = token.end()
    if expect != "done" and not _ends_json_line(text, pos, end, expect, fresh):
        return (), (), line_start
    return tuple(opens), tuple(closes), pos


def _ends_json_line(text: str, pos: int, end: int, expect: str, fresh: bool) -> bool:
    """Whether a line of JSON that is not done may end at `pos`: only blanks follow, after a
    value (`"a": 1`, `"a": 1,` in pretty-printed JSON) or an opening bracket (`"a": {`). A line
    with anything else in it, or that stops after a key or a colon, is not read as JSON at all."""
    if _BLANKS.match(text, pos, end).end() != end:
        return False
    return expect in ("comma", "key") or (expect == "value" and fresh)


def _end_in_string(match: re.Match[str], value: str, origin: tuple[str, int]) -> int | None:
    """Where an unquoted value ends when it sits inside a JSON string value: at the string's
    closing quote, when its line reads as JSON (`_json_strings`) from where the JSON starts up to
    the end of the value (`{"q": "a=1&token=x","n":1}`). `origin` is the text the match was
    found in, or the one that text was cut from, and where it starts there. None otherwise, and
    when a backslash comes before the first quote: it may escape it (`\\"`), or another
    (`\\\\"`).

    `<redacted>` with JSON tokens after its quote is redaction's own output and ends there,
    however the line reads: a value hidden whole earlier on the line may have taken a quote
    with it, after which a second pass no longer reads the line as JSON."""
    end = value.find('"')
    if end < 0 or "\\" in value[:end]:
        return None
    if value[:end] == REDACTED:
        return end if _JSON_RUN.fullmatch(value, end + 1) else None
    text, shift = origin
    start = shift + match.start(3)
    opens, closes, valid = _json_strings(text, text.rfind("\n", 0, start) + 1)
    index = bisect.bisect_left(opens, start) - 1
    if index < 0 or closes[index] != start + end or shift + match.end(3) > valid:
        return None
    return end


def _redact_line_pair(match: re.Match[str]) -> str:
    return _redact_pair(match, 0, (match.string, 0))


def _redact_pair(
    match: re.Match[str], depth: int = 0, origin: tuple[str, int] | None = None
) -> str:
    """`origin`: the text whose lines may be read as JSON for the pair (`_end_in_string`) and
    where `match.string` starts in it; None inside a value, whose own quotes say nothing."""
    key, separator, value = match.group(1), match.group(2), match.group(3)
    if _names_a_setting(match):
        return match.group(0)
    if not _is_secret_key(key):
        kept = _redact_inside(value, depth)
        return f"{key}{separator}{_hide_dsn_user(kept) if _is_dsn_key(key) else kept}"
    if _BLOCK_HEADER.fullmatch(value):
        return match.group(0)
    if value[0] == "[":
        closed = len(value) > 1 and value.endswith("]")
        items = (value[1:-1] if closed else value[1:]).strip()
        if (closed and not items) or _is_plain(items):
            return match.group(0)  # an empty list is evidence
        return f"{key}{separator}{REDACTED}"
    quote = value[0] if value[0] in "\"'" else ""
    closed = bool(quote) and len(value) > 1 and value.endswith(quote)
    inner = value[1:-1] if closed else value[len(quote) :]
    if (
        (closed and not inner)
        or _is_plain(inner)
        or _NOT_SET.match(match.string, match.start(3) + len(quote))
    ):
        return match.group(0)  # an empty password is evidence, "not set" and `true` are not secrets
    if (
        not depth
        and not quote
        and key.lower() in ("credentials", "auth")
        and _LOOKS_LIKE_PAIR.match(value)
        and _PAIR_FOLLOWS.match(match.string, match.end(3))
    ):
        # `invalid credentials: user=bob retries=3` is a message; its own pairs are still
        # judged, so `auth: token=x user=bob` hides x
        return f"{key}{separator}{_redact_inside(value, depth)}"
    if quote:
        return f"{key}{separator}{quote}{REDACTED}{quote if closed else ''}"
    if origin and depth < MAX_NESTING and (end := _end_in_string(match, value, origin)) is not None:
        # `{"q": "a=1&token=x"}`: the string's closing quote, comma and braces stay, and the
        # JSON after them has its pairs judged as a kept value's are, each a level deeper, so
        # past `MAX_NESTING` such strings in one run the rest of the run is hidden whole, as
        # before. A plain value before the quote is hidden too, so a second pass reads
        # `<redacted>` there and ends it the same.
        rest = value[end + 1 :]
        place = (origin[0], origin[1] + match.start(3) + end + 1)
        return f'{key}{separator}{REDACTED}"{_redact_inside(rest, depth, place) if rest else ""}'
    return f"{key}{separator}{REDACTED}"


MAX_NESTING = 3


def _redact_inside(value: str, depth: int, origin: tuple[str, int] | None = None) -> str:
    """The pairs inside a value that is kept (`TOKEN_URL=https://x/cb?token=abc`): one pass of
    `_KEY_VALUE` takes the whole value and never looks inside it. Past `MAX_NESTING` levels a value
    still holding a pair is hidden whole, which bounds the work. `origin`: where `value` was cut
    from when it is the JSON after a string (`_redact_pair`)."""
    if depth >= MAX_NESTING:
        return REDACTED if _KEY_VALUE.search(value) else value
    return _KEY_VALUE.sub(lambda inner: _redact_pair(inner, depth + 1, origin), value)


# A DSN's user with no password is its key (Sentry: `https://<key>@o0.ingest.sentry.io/1`, also
# with an empty password, `<key>:@`). Only under a DSN key: elsewhere a bare user
# (`ssh://git@github.com`) is no secret.
_DSN_USER = re.compile(
    r"((?<![a-z0-9+.-])[a-z][a-z0-9+.-]{0,31}://)(?!<redacted>@)[^\s:/@\"']{1,128}:?@"
    r"(?=[A-Za-z0-9._\[\]:-]+(?:[/?#\s\"']|$))",
    _FLAGS,
)


@lru_cache(maxsize=1024)
def _is_dsn_key(key: str) -> bool:
    words = WORDS.findall(key)
    return bool(words) and words[-1].lower() == "dsn"


def _hide_dsn_user(value: str) -> str:
    """The userinfo of a DSN with no password hidden, its host kept; a `user:password@` is left to
    the URL pattern, which keeps the user."""
    return _DSN_USER.sub(rf"\1{REDACTED}@", value)


def _redact_flag(match: re.Match[str]) -> str:
    """`--db-password hunter2`: a secret option with its value as the next word."""
    name, space, value = match.group(1), match.group(2), match.group(3)
    quote = value[0] if value[0] in "\"'" else ""
    inner = value.strip(quote) if quote else value
    if not (_is_secret_key(name) or name.lower() == "--key") or _is_plain(inner) or not inner:
        return match.group(0)
    return (
        f"{name}{space}{quote}{REDACTED}{quote if len(value) > 1 and value.endswith(quote) else ''}"
    )


_COOKIE_ATTRS = {"path", "domain", "expires", "max-age", "samesite", "secure", "httponly"}


def _redact_cookie(match: re.Match[str]) -> str:
    value = match.group(2)
    quote = value[0] if value[:1] in ('"', "'") else ""
    closed = bool(quote) and len(value) > 1 and value.endswith(quote)
    inner = value[1:-1] if closed else value[len(quote) :]
    parts = []
    for part in inner.split(";"):
        name, eq, cookie = part.partition("=")
        if eq and name.strip().lower() not in _COOKIE_ATTRS and cookie.strip() != REDACTED:
            part = f"{name}={REDACTED}"
        parts.append(part)
    return f"{match.group(1)}{quote}{';'.join(parts)}{quote if closed else ''}"


LIST_MAX_LINES = 20
# A line that starts a new log record (a date, syslog's `Nov  2 `, a level) ends a flow list.
_NEW_RECORD = (
    r"[ \t]*+(?:\d{4}-\d\d-\d\d|[A-Z][a-z]{2} [ \d]\d "
    r"|(?:TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|CRITICAL)\b)"
)
# The start of a key `_KEY_VALUE` or `_COOKIE` takes, with its separator: a multi-line flow list
# runs on after its `]` up to the next one. Inside a run of name characters only its first one
# reads ahead, and only over that run, so the check stays linear.
_NEXT_KEY = (
    r"(?<![A-Za-z0-9_.-])"
    rf"(?=[A-Za-z0-9_.-]{{0,96}}?(?:{_SECRET_HINT}|cookie))"
    r"[A-Za-z0-9_.-]{1,96}[\"']?\]?[ \t]*+(?:=>|:=|==|[=:])"
)
# The `[… N chars cut]` line `_cut_line` puts into a line over 4,000 characters, after its lead.
_CUT_NOTE_LINE = r"[^\[\]\n]{0,200}+\[… [0-9]+ chars cut\]\n"
# A flow list's `]` ends the value when closers and at most one quote follow it, then whitespace or
# the end; otherwise the value runs on to the next whitespace (`[aB3],xY9` is a password).
_LIST_END = r"\](?:(?=[,;})\]]*+[\"']?(?:\s|\Z))|\S*+)"
# password=x, "password": "x y", password: x, [password] => x, SECRET_KEY_BASE=x, X-Api-Key: x,
# api_keys: ["a", "b"]. The key is judged by `_is_secret_key`; the cheap lookahead keeps Python
# out of unrelated pairs. An unquoted value runs to whitespace (so it fails safe on `&`, `,`,
# quotes; `_end_in_string` gives back the closing quote of a string it sits in), a flow list to a
# `]` that ends the value (`_LIST_END`, so compact JSON after a list goes too), or to the line's
# end when it has no `]`. Neither starts with a cut note.
#
# A list left open at the end of its line runs on to its `]` over at most 20 lines, stepping over
# a cut note, unless another `[` or `]` or a new log record comes first; each scan stops at the
# next bracket, so no two overlap. Where the lines would hold a pair that runs on past the `]`,
# it is not taken (and the line alone counts, as before): its last line holds no `=` or `:`
# before the `]` (`token=a]b`), and no line ends in a separator, whose value is on the next line
# (`api_key:` and then `'x]y'`). It ends at that `]` when closers and a quote follow, then
# whitespace or the next key (`],"password": "x"` is the next pair), else runs on as `_LIST_END`
# does but stops before the next key. A `]` followed by a separator is a PHP key
# (`[\ncredential] => x`), not the end of a list. Such a list redacted, `<redacted>` followed by
# the next key is a value of its own, so a second pass changes nothing.
_KEY_VALUE = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    rf"(?=[A-Za-z0-9_.-]{{0,96}}?{_SECRET_HINT})"
    r"([A-Za-z0-9_.-]{1,96})"
    r"([\"']?\]?[ \t]*+(?:=>|:=|==|[=:])[ \t]*+(?:(?:\n[ \t]*+){1,8}(?<=[ \t]))?)"
    r"(?!\[… )"
    r"(\"(?:[^\"\\\n]|\\.)*+\"?|'(?:[^'\\\n]|\\.)*+'?"
    rf"|\[(?:(?:[^\[\]\n]*+(?<![=:>])\n|{_CUT_NOTE_LINE})(?!{_NEW_RECORD})){{1,{LIST_MAX_LINES}}}+"
    r"[^\[\]\n=:]*+\](?![ \t]*+(?:=>|:=|==|[=:]))"
    rf"(?:(?=[,;}})\]]*+[\"']?(?:\s|\Z|{_NEXT_KEY}))|(?:(?!{_NEXT_KEY})\S)*+)"
    rf"|\[[^\]\n]*+(?:{_LIST_END}|(?=\n|\Z))"
    rf"|<redacted>(?=[,;}})\]]*+[\"']?(?:\s|\Z|{_NEXT_KEY}))"
    r"|[^\s\"'][^\s]*+)",
    _FLAGS,
)


# The opening tag of an element named like a secret or with a `key` or `name` attribute; each
# lookahead stops at the next `<` or `>`.
_ELEMENT_TAG = (
    rf"<(?=[A-Za-z0-9_.:-]{{0,64}}?{_SECRET_HINT}"
    r"|[A-Za-z0-9_.:-]{1,64}\s[^<>]{0,256}?(?<![A-Za-z0-9_.:-])(?:key|name)[ \t]*=)"
    r"([A-Za-z0-9_.:-]{1,64})((?:\s[^<>]{0,256}+)?)>"
)
# <password>x</password>, <entry key="db.password">x</entry>, with attributes, the value on lines
# of its own or in CDATA. The text runs to the next `<`, and CDATA for at most 512 characters to
# its `]]>` or the next CDATA, so no text is read for more than two matches. A CDATA without its
# `]]>` counts only up to the end of the text and holds no `<`, so it never takes in the elements
# after it.
_ELEMENT_TEXT = (
    r"([^<]*+(?:<!\[CDATA\[(?:(?:[^\]<]|\](?!\]>)|<(?!!\[CDATA\[)){0,512}+\]\]>[^<]*+"
    r"|[^<\n]{0,512}+(?=\Z)))?)"
)
_ELEMENT = re.compile(_ELEMENT_TAG + _ELEMENT_TEXT + r"(</\1>)", _FLAGS)
# In the head window of a cut line, whose end is a cut and not the end of the value, an element
# still open there takes the rest of the window (`_redact_all(cut_end=True)`).
_ELEMENT_TO_CUT = re.compile(_ELEMENT_TAG + _ELEMENT_TEXT + r"(</\1>|\Z)", _FLAGS)
_ELEMENT_OPEN = re.compile(_ELEMENT_TAG, _FLAGS)
_CDATA = re.compile(r"<!\[CDATA\[(.*?)(?:\]\]>|\Z)", re.DOTALL)


def _is_secret_element(name: str, attributes: str) -> bool:
    return _is_secret_key(name) or any(
        _is_secret_key(attribute.group(2)) for attribute in _NAME_ATTRIBUTE.finditer(attributes)
    )


def _redact_element(match: re.Match[str]) -> str:
    """`<password>x</password>`, `<entry key="db.password">x</entry>`: an XML element named like
    a secret or with a `key` or `name` attribute that is one, its attributes and the whitespace
    around its value kept. A value in CDATA is judged by its text and hidden with the CDATA."""
    name, attributes, content, close = match.groups()
    value = _CDATA.sub(r"\1", content).strip()
    if not _is_secret_element(name, attributes) or not value or _is_plain(value):
        return match.group(0)
    before = match.string[match.start() - 1 : match.start()]
    if not close and ("\n" in content or before.isalnum() or before in ("_", "$")):
        # still open at a cut: only a tag on its own counts, not a type parameter after a name
        # (`List<AccessToken>`, `ArrayList<com.acme.ApiToken>`); its text holds no `<`, so no
        # element is skipped by keeping it
        return match.group(0)
    lead, trail = content[: len(content) - len(content.lstrip())], content[len(content.rstrip()) :]
    return f"<{name}{attributes}>{lead}{REDACTED}{trail}{close}"


def _redact_setting(match: re.Match[str]) -> str:
    """`<add key="Password" value="x"/>`, `<add value="x" key="Password"/>`: a value attribute
    next to a key or name that is secret."""
    value = match.group("value")
    closed = len(value) > 1 and value.endswith(value[0])
    inner = value[1:-1] if closed else value[1:]
    if not _is_secret_key(match.group("name")) or not inner or _is_plain(inner):
        return match.group(0)
    return f"{match.group('lead')}{value[0]}{REDACTED}{value[0] if closed else ''}"


def _redact_jwt(match: re.Match[str]) -> str:
    """A JWT: `eyJ` and at least five more characters, then two more segments of five or more.
    `_JWT` takes the whole first run, so starts inside it (`-eyJ-eyJ…`) are not tried again; they
    end where it ends and could not match where it did not."""
    return REDACTED if match.group(0).count(".") == 2 else match.group(0)


# A token starts where no word does, or right after a percent-encoded byte
# (`?next=%3Fk%3Dsk_live_…`), whose last hex digit `\b` takes for the end of a word (#416), after
# one encoded again any number of times (`%253D`, `%25253D`), after a JSON-escaped line break, tab,
# form feed or backspace (`"started\nsk_live_…"`), whose letter reads as a word (#440, #452), the
# same percent-encoded (`%5Cn`), or after a `\uXXXX` escape, whose last hex digit does the same:
# Go's encoding/json writes `&`, `<` and `>` as `&`, `<` and `>` (#452). Each
# lookbehind is of fixed width, so a depth of three or more is told by its last `2525` and not by
# the `%` before it.
_HEX2 = "[0-9A-Fa-f]{2}"
_AFTER_ESCAPE = (
    rf"(?:(?<=%{_HEX2})|(?<=%25{_HEX2})|(?<=2525{_HEX2})"
    r"|(?<=\\[nrtfb])|(?<=%5[Cc][nrtfb])|(?<=\\u[0-9A-Fa-f]{4}))"
)
_TOKEN_START = rf"(?:(?<!\w)|{_AFTER_ESCAPE})"


def _starting(prefix: str, start: str = _TOKEN_START) -> str:
    """`prefix` (of fixed width) where a token may start: written prefix first, with the start
    checked behind it, because a pattern that begins with a lookbehind loses `re`'s fast scan for
    its literal start, which slowed all redaction about 1.7 times (#416)."""
    return rf"{prefix}(?<={start}{prefix})"


_JWT = re.compile(
    _starting("eyJ") + r"[A-Za-z0-9_-]{5,}+(?:\.[A-Za-z0-9_-]{5,}+\.[A-Za-z0-9_-]{5,}+)?"
)
# GitLab's other tokens, in the lengths GitLab's own secret detection rules give: most are the
# prefix and 20 of `A-Za-z0-9_-`, a routable one 27 or more and the suffix `glpat-` has too.
# `_GITLAB` takes a prefix and its whole run of token characters with the `.` parts after it, and
# `_redact_gitlab` judges that run by `_GITLAB_SHAPE`, so each run is read once however its
# prefixes repeat (`glrt-glrt-…`).
_GITLAB_PREFIX = (
    r"(?:gl(?:rtr?|rct|dt|ft|soat|ffct|ptt|imt|agent|oas|cbt|wt|iam(?:rt|ac))-|GR1348941)"
)
_GITLAB_PREFIX_AT = re.compile(_GITLAB_PREFIX)
_GITLAB = re.compile(rf"{_GITLAB_PREFIX}[A-Za-z0-9_-]{{20,}}+(?:\.[A-Za-z0-9_-]++)*+")
_GITLAB_SHAPE = re.compile(
    r"gl(?:rtr?|dt|agent|wt)-[A-Za-z0-9_-]{27,300}+\.[0-9a-z]{2}\.[0-9a-z]{9}"
    r"|glft-[0-9a-f]{64}-[0-9]{1,20}"  # before the 20-character `glft-`, which it starts with
    r"|gl(?:rtr?-(?:t[1-3]_)?|rct-|dt-|ft-|soat-|ffct-)[A-Za-z0-9_-]{20}"
    r"|glptt-(?:[A-Za-z0-9_-]{40}|[A-Za-z0-9_-]{20})"
    r"|glimt-[0-9a-z]{20,25}"
    r"|glagent-[A-Za-z0-9_-]{50}"
    r"|gloas-[A-Za-z0-9_-]{64}"
    r"|glcbt-[0-9a-zA-Z]{1,5}_[A-Za-z0-9_-]{20}"
    r"|gliam(?:rt|ac)-[A-Za-z0-9_-]{43}\.[A-Za-z0-9_-]{43}"
    r"|GR1348941[A-Za-z0-9_-]{20}"
)
# the next prefix inside a run, after a character that is no letter or digit (`x-gldt-foo-glrt-…`)
_NEXT_GITLAB = re.compile(rf"(?<![A-Za-z0-9]){_GITLAB_PREFIX}")


def _redact_gitlab(match: re.Match[str]) -> str:
    """Every token in the run that is one of GitLab's shapes and ends where the run's token
    characters end, before one of its `.` parts (`glrt-<20>.` at the end of a sentence), or
    before `-` or `_` and the next prefix (`glimt-<23>-glrt-<20>`). Each try starts at a prefix
    the one before did not take, and a shape is at most about 300 characters, so a run costs a
    bounded amount of work per character."""
    run, out, kept, at = match.group(0), [], 0, 0
    while at < len(run):
        shape = _GITLAB_SHAPE.match(run, at)
        end = shape.end() if shape else at
        if shape and (
            end == len(run)
            or run[end] == "."
            or (run[end] in "-_" and _GITLAB_PREFIX_AT.match(run, end + 1))
        ):
            out.append(run[kept:at] + REDACTED)
            kept = at = end
            continue
        following = _NEXT_GITLAB.search(run, at + 1)
        if following is None:
            break
        at = following.start()
    return "".join(out) + run[kept:]


# Cookie and Set-Cookie, as a header, JSON or logfmt (request_Cookie, "cookie": "..."): the cookie
# names stay, their values go
_COOKIE = re.compile(
    r"((?<![A-Za-z0-9])[A-Za-z0-9_-]{0,32}cookies?[\"']?[ \t]*+[:=][ \t]*+)"
    r"(\"(?:[^\"\\\n]|\\.)*+\"?|'(?:[^'\\\n]|\\.)*+'?|[^\n]*)",
    _FLAGS,
)

Replacement = str | Callable[[re.Match[str]], str]
_PATTERNS: list[tuple[re.Pattern[str], Replacement]] = [
    # <add key="Password" value="x"/>, <input name="password" value="x">, and the value first
    (
        re.compile(
            r"(?<![A-Za-z0-9_.:-])"
            r"(?P<lead>(?:key|name)[ \t]*=[ \t]*(?P<q>[\"'])(?P<name>[A-Za-z0-9_.:-]{1,96})(?P=q)"
            r"\s{1,64}+value[ \t]*=[ \t]*)(?P<value>\"[^\"\n]*+\"?|'[^'\n]*+'?)",
            _FLAGS,
        ),
        _redact_setting,
    ),
    (
        re.compile(
            r"(?<![A-Za-z0-9_.:-])(?P<lead>value[ \t]*=[ \t]*)(?P<value>\"[^\"\n]*+\"?|'[^'\n]*+'?)"
            r"(?=\s{1,64}+(?:key|name)[ \t]*=[ \t]*"
            r"(?P<q>[\"'])(?P<name>[A-Za-z0-9_.:-]{1,96})(?P=q))",
            _FLAGS,
        ),
        _redact_setting,
    ),
    # before the value patterns, so a value holding a token (`password=ghp_...!tail`) is one
    # redaction
    (_KEY_VALUE, _redact_line_pair),
    (_ELEMENT, _redact_element),
    # --db-password hunter2, --api-key abc, --key abc: a secret option followed by its value
    (
        re.compile(
            r"(?<![A-Za-z0-9_-])(--[A-Za-z0-9_-]{0,64}?(?:password|passwd|secret|token|key)"
            r"[A-Za-z0-9_-]{0,32})([ \t]+)(?!-)"
            r"(\"(?:[^\"\\\n]|\\.)*+\"?|'(?:[^'\\\n]|\\.)*+'?|[^\s]+)",
            _FLAGS,
        ),
        _redact_flag,
    ),
    # Authorization: <anything>, whatever the scheme, quoted or not
    (
        re.compile(
            r"(authorization[\"']?+[ \t]*+[:=][ \t]*+[\"']?+)"
            rf"(?!(?:{_SCHEME}[ \t]+)?[\"']?<redacted>)"
            rf"((?:{_SCHEME}[ \t]+)?)[\"']?[^\s\"']+",
            _FLAGS,
        ),
        rf"\1\2{REDACTED}",
    ),
    (_COOKIE, _redact_cookie),
    # a bare "Bearer xyz" / "Basic xyz" must look like a credential, not prose
    (
        re.compile(
            r"\b(bearer|basic)(\s+[\"']?)(?=[A-Za-z0-9._~+/=-]{8,}(?![^\s\"']))"
            r"(?=[^\s\"']*[0-9._~+/=-])[^\s\"']+",
            _FLAGS,
        ),
        rf"\1\2{REDACTED}",
    ),
    (re.compile(_starting("gh[pousr]_") + r"[A-Za-z0-9]{20,}"), REDACTED),
    (re.compile(_starting("sk-") + r"(?:or-)?[A-Za-z0-9_-]{16,}"), REDACTED),
    (re.compile(_starting("xox[abcprs]-") + r"[A-Za-z0-9-]{10,}"), REDACTED),
    # Slack's refresh and legacy tokens and app-level tokens: "xoxo" is also a word, so a number
    # must follow the prefix as it does in every one of these tokens
    (re.compile(_starting("x(?:ox[eo]|app)-") + r"[0-9]{1,16}-[A-Za-z0-9-]{8,}+"), REDACTED),
    (_JWT, _redact_jwt),
    (re.compile(_starting(r"ya29\.") + r"[A-Za-z0-9_.-]{20,}+"), REDACTED),  # Google OAuth
    (re.compile(_starting(r"AQ\.") + r"[A-Za-z0-9_-]{40,}+"), REDACTED),  # Google auth keys
    (
        re.compile(
            _starting("[rs]k_") + r"(?:live|test|(?<=sk_)org(?:_live|_test)?)_[A-Za-z0-9]{20,}+"
        ),
        REDACTED,
    ),
    # LangSmith personal access tokens and service keys, as TruffleHog's detector reads them
    (re.compile(_starting("lsv2_") + r"(?:pt|sk)_[a-f0-9]{32}_[a-f0-9]{10}(?!\w)"), REDACTED),
    (re.compile(_starting("npm_") + r"[A-Za-z0-9]{36}(?![A-Za-z0-9])"), REDACTED),
    # Pushbullet: `o.` and 32 letters and digits of both cases, on their own, so a domain such as
    # `cdn.o.example` and a hex digest after `o.` stay
    (
        re.compile(
            _starting(r"o\.", rf"(?:(?<![A-Za-z0-9_.-])|{_AFTER_ESCAPE})")
            + r"(?=[A-Za-z0-9]{0,31}[A-Z])(?=[A-Za-z0-9]{0,31}[a-z])"
            r"[A-Za-z0-9]{32}(?![A-Za-z0-9_-])(?!\.[A-Za-z0-9])"
        ),
        REDACTED,
    ),
    # Tokens taken glued to the word before them too (`tokenghp_…`, `x_glrt-…`; #417), where the
    # prefix with the rest of the shape is no word's ending: a word ends in `ghs_` (`highs_`), so
    # glued GitHub tokens need GitHub's full 36 characters. They come after the tokens with a
    # start of their own, so one of those whose characters happen to hold such a prefix is hidden
    # whole first.
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{36}(?![A-Za-z0-9])"), REDACTED),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), REDACTED),
    # glued, a Slack token needs the number its id starts with (`detoxoxc-…` is a word)
    (re.compile(r"xox[abcprs]-[0-9][A-Za-z0-9-]{9,}"), REDACTED),
    (re.compile(r"xoxd-[A-Za-z0-9%+/=_-]{20,}+"), REDACTED),  # Slack's browser cookie `d`
    (re.compile(r"AIza[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])"), REDACTED),
    # GitLab PATs; a routable one ends in `.`, a 2-character version, `.` and 9 more characters
    # (an earlier build: `.` and 9 characters)
    (
        re.compile(r"glpat-[A-Za-z0-9_-]{20,}+(?:\.[0-9a-z]{2}\.[0-9a-z]{9}|\.[0-9a-z]{9})?"),
        REDACTED,
    ),
    (_GITLAB, _redact_gitlab),
    (re.compile(r"pbkdf2_sha(?:1|256|512)\$[0-9]{1,8}\$[^\s\"']+"), REDACTED),
    # scheme://user:password@host: keep the user. The password may hold `@` and `/`; it ends at
    # the first `@` that is followed by a host and then `/`, `?`, `#`, whitespace or the end. A
    # match starts at the `://`, after any character a scheme ends in, so a scheme glued to the
    # word before it counts too (`xxxxhttps://`, `:-postgresql://`; #417) and starts cannot
    # overlap.
    (
        re.compile(
            r"((?<=[a-z0-9+.-])://[^\s:/@]{0,64}:)(?!<redacted>@)\S{1,128}?(@)"
            r"(?=[A-Za-z0-9._\[\]:-]+(?:[/?#\s]|$))",
            _FLAGS,
        ),
        rf"\1{REDACTED}\2",
    ),
    (re.compile(_starting("A[KS]IA") + r"[0-9A-Z]{16,}\b"), REDACTED),
    (
        re.compile(
            _starting(r"\d", rf"(?:(?<![0-9])|{_AFTER_ESCAPE})")
            + r"\d{7,9}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])"
        ),
        REDACTED,
    ),
    # crypt hashes: htpasswd/basic-auth ($apr1$, $2y$, $5$, $6$, $y$, $argon2id$)
    (re.compile(r"\$(?:apr1|2[aby]|5|6|y|argon2(?:id|i|d))\$[^\s\"']+"), REDACTED),
    # md5crypt `$1$salt$hash`, its full shape, so `$1$2` in a script stays
    (re.compile(r"\$1\$[./0-9A-Za-z]{1,8}\$[./0-9A-Za-z]{22,}+"), REDACTED),
]

BLOCK_MAX_LINES = 200
# A log prefix before the YAML: a timestamp, syslog's `Nov  2 10:00:00 host app: `, a Compose
# `web-1  | ` or a `[tag] `. It is skipped
# before measuring indentation, so `web-1  |   x` is indented by 2.
_LOG_PREFIX = re.compile(
    r"(?:\d{4}-\d\d-\d\d[T ][\d:.,]{1,20}Z?[ \t]"
    r"|[A-Z][a-z]{2} [ \d]\d \d\d:\d\d:\d\d [^\s:]{1,64} [^\s:\[]{1,64}(?:\[\d{1,10}\])?: "
    r"|[A-Za-z0-9_.-]{1,64}[ \t]*\|[ \t]"
    r"|\[[^\]\n]{0,40}\][ \t])?"
)
_KEY_REVERSED = re.compile(r"[A-Za-z0-9_.-]{1,96}")


def _lead(line: str) -> str:
    """The log prefix and indentation of `line`."""
    rest = line[_LOG_PREFIX.match(line).end() :]
    return line[: len(line) - len(rest.lstrip(" \t"))]


_HEADER_END = re.compile(rf"{_BLOCK_INDICATOR}\Z")
_LIST_ITEM = re.compile(r"-[ \t]+")


def _key_column(line: str, key_start: int) -> int | None:
    """The column of a key at `key_start` that starts the line's content, after the log prefix,
    the indentation and a list item's `- ` (`- password: |`, whose siblings sit at that column);
    None for a key after other text (`Error: auth: >`, `echo token: >`)."""
    column = len(_lead(line))
    item = _LIST_ITEM.match(line, column)
    if item:
        column = item.end()
    quoted = line[column : column + 1] in ('"', "'")
    if key_start != column + quoted:
        return None
    return column - _LOG_PREFIX.match(line).end()


def _block_header(line: str) -> tuple[int, bool] | None:
    """If `line` ends in `<secret key>: |` (or `>`, any prefix before it), the indentation a
    block line must exceed (the key's column when it starts the line's content, else the line's
    indentation) and whether the key starts it; otherwise None. Read from the end so it costs
    one pass."""
    if len(line) > 400 or ("|" not in line and ">" not in line):
        return None
    text = line.split(" #", 1)[0].rstrip()
    header = _HEADER_END.search(text)
    if header is None:
        return None
    text = text[: header.start()].rstrip(" \t")
    if not text.endswith(":"):
        return None
    text = text[:-1].rstrip(" \t").removesuffix('"').removesuffix("'")
    key = _KEY_REVERSED.match(text[::-1][:96])
    if key is None or not _is_secret_key(key.group(0)[::-1]):
        return None
    before = text[: len(text) - key.end()]
    if before and not before[-1].isspace() and before[-1] not in "\"'":
        return None
    column = _key_column(line, len(before))
    if column is None:
        return len(_lead(line)) - _LOG_PREFIX.match(line).end(), False
    return column, True


# `password: |  hunter2`: a block header with more than a comment after it on its line, as when
# stripping U+0085, VT or FF joined the header and its first block line (#417)
_INLINE_BLOCK = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    rf"(?=[A-Za-z0-9_.-]{{0,96}}?{_SECRET_HINT})"
    rf"([A-Za-z0-9_.-]{{1,96}})[\"']?[ \t]*+:[ \t]*+{_BLOCK_INDICATOR}+[ \t]++(?=[^\s#])",
    _FLAGS,
)


def _inline_block(line: str) -> tuple[str, int | None] | None:
    """If `line` holds `<secret key>: |` (or `>`) and a value after it, the line with the rest
    after the header hidden, and the indentation a block line must exceed when the key starts
    the line's content (`_key_column`); a header after other text (`echo token: > /tmp/out`,
    `| Token: | Description |`) has no block under it. None when there is no such header. Each
    search starts after the last header, so the line is read once."""
    if ":" not in line:
        return None
    start = 0
    while (header := _INLINE_BLOCK.search(line, start)) is not None:
        value = line[header.end() :].rstrip()
        if (
            _is_secret_key(header.group(1))
            and value != REDACTED
            and not _is_plain(value)
            and not _NOT_SET.match(value)
        ):
            key_start = header.start(1)
            return line[: header.end()] + REDACTED, _key_column(line, key_start)
        start = header.end()
    return None


# What a line right after a block header can be other than its value: a key, a list item, a
# comment or a document marker
_YAML_STRUCTURE = re.compile(
    r"[ \t]*+(?:-(?:[ \t]|\Z)|#|(?:---|\.\.\.)(?:[ \t]|\Z)"
    r"|[\"']?[A-Za-z0-9_.-]{1,96}[\"']?[ \t]*+:(?:[ \t]|\Z))"
)


_NEW_RECORD_START = re.compile(_NEW_RECORD)


def _bare_value(lines: list[str], header: int) -> int | None:
    """The line after a block header with no deeper lines, past one blank line, when it is the
    header's value written at the key's indentation, as a VT or FF that was a line break leaves
    it (`password: |` and then `4242`): it has text, no log prefix of its own, and is no YAML
    structure or the start of a new log record (`INFO …`). None otherwise."""
    at = header + 1
    if at + 1 < len(lines) and not lines[at].strip():
        at += 1
    if at >= len(lines):
        return None
    body = lines[at]
    if (
        body.strip()
        and _LOG_PREFIX.match(body).end() == 0
        and body.strip() != REDACTED
        and _YAML_STRUCTURE.match(body) is None
        and _NEW_RECORD_START.match(body) is None
    ):
        return at
    return None


def _redact_blocks(text: str) -> tuple[str, int]:
    """YAML block scalars under a secret key (`password: |`): every line of the block, blank lines
    kept, up to the first line that is not indented deeper than the key (at most 200 lines), and
    a value on the header's own line. That value is judged first, so a joined line that ends in
    another header (`password: |  x token: |`) still hides it."""
    if "|" not in text and ">" not in text:
        return text, 0
    lines, count = text.split("\n"), 0
    for i, line in enumerate(lines):
        inline, starts_line = _inline_block(line), False
        if inline is None:
            header = _block_header(line)
            indent, starts_line = header if header else (None, False)
        else:
            lines[i], indent = inline
            count += 1
        if indent is None:
            continue
        end, last = i + 1, i
        while end < min(len(lines), i + 1 + BLOCK_MAX_LINES):
            body = lines[end]
            if body.strip():
                if len(_lead(body)) - _LOG_PREFIX.match(body).end() <= indent:
                    break
                last = end
            end += 1
        if starts_line and last == i:
            last = _bare_value(lines, i) or last
        changed = False
        for j in range(i + 1, last + 1):
            body = lines[j]
            lead = _lead(body)
            if body.strip() and body[len(lead) :] != REDACTED:
                lines[j] = lead + REDACTED
                changed = True
        count += changed
    return "\n".join(lines), count


_PEM_BEGIN = re.compile(r"-----BEGIN [A-Z ]{0,30}PRIVATE KEY-----")
_PEM_END = re.compile(r"-----END [A-Z ]{0,30}PRIVATE KEY-----")
_PEM_BODY = re.compile(r"(?=[a-z]*[0-9A-Z+/])[A-Za-z0-9+/]{4,}={0,2}|(?:Proc-Type|DEK-Info):.*")
_B64_ANY = re.compile(r"[A-Za-z0-9+/]{60}")
_B64_LINE = re.compile(r"(?=[^A-Z]*[A-Z])(?=[^a-z]*[a-z])[A-Za-z0-9+/]{60,80}={0,2}")  # not all hex
PEM_MAX_LINES = 100


_LINE_PREFIX = re.compile(
    r"[ \t]*(?:\[[^\]\n]{0,40}\][ \t]*|\d{4}-\d\d-\d\d[T ][\d:.,]{1,20}Z?[ \t]+)?"
)


def _body(line: str) -> str:
    """The line without leading whitespace, a `[...]` tag or a timestamp."""
    return line[_LINE_PREFIX.match(line).end() :]


def _is_b64(line: str) -> bool:
    line = _body(line)
    return 60 <= len(line) <= 82 and _B64_LINE.fullmatch(line) is not None


def _redact_keys(text: str) -> tuple[str, int]:
    """Private key blocks: BEGIN to its END line, or the body lines that follow a lone BEGIN
    (at most 100); and 3+ consecutive 64-character base64 lines, a key body whose BEGIN was cut."""
    if "PRIVATE KEY-----" not in text and not _B64_ANY.search(text):
        return text, 0
    lines, out, count, i = text.split("\n"), [], 0, 0
    while i < len(lines):
        line = lines[i]
        begin = _PEM_BEGIN.search(line)
        if begin:
            end_at = next(
                (
                    j
                    for j in range(i, min(i + PEM_MAX_LINES, len(lines)))
                    if _PEM_END.search(lines[j])
                ),
                None,
            )
            if end_at is not None:
                tail = lines[end_at][_PEM_END.search(lines[end_at]).end() :]
                out.append(line[: begin.start()] + REDACTED + tail)
                i, count = end_at + 1, count + 1
                continue
            j = i + 1
            while j < min(i + 1 + PEM_MAX_LINES, len(lines)) and _PEM_BODY.fullmatch(
                _body(lines[j])
            ):
                j += 1
            out.append(line[: begin.start()] + REDACTED)
            i, count = j, count + 1
            continue
        j = i
        while j < len(lines) and _is_b64(lines[j]):
            j += 1
        if j - i >= 3:
            out.append(REDACTED)
            i, count = j, count + 1
            continue
        out.append(line)
        i += 1
    return "\n".join(out), count


def _apply(pattern: re.Pattern[str], replacement: Replacement, text: str) -> tuple[str, int]:
    """`pattern.sub`, counting the values each changed match hid: one match can hide several (the
    cookies of one header, the pairs inside a kept value), and counts at least one."""
    changed = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal changed
        out = replacement(match) if callable(replacement) else match.expand(replacement)
        if out != match.group(0):
            changed += max(1, out.count(REDACTED) - match.group(0).count(REDACTED))
        return out

    return pattern.sub(replace, text), changed


# A window of a line over 4,000 characters is never read as JSON: an edge of the window is no
# edge of the line, and the line's other part may hold what rules the JSON out.
_PATTERNS_IN_CUT = [(p, _redact_pair if r is _redact_line_pair else r) for p, r in _PATTERNS]
_PATTERNS_TO_CUT = [(_ELEMENT_TO_CUT if p is _ELEMENT else p, r) for p, r in _PATTERNS_IN_CUT]


def _redact_all(text: str, cut_end: bool = False, in_cut: bool = False) -> tuple[str, int]:
    """All patterns over `text`; with `in_cut`, `text` is a window of a line over 4,000
    characters; with `cut_end`, it is the head window, whose end is the window's edge."""
    text, total = _redact_blocks(text)
    text, count = _redact_keys(text)
    total += count
    patterns = _PATTERNS_TO_CUT if cut_end else _PATTERNS_IN_CUT if in_cut else _PATTERNS
    for pattern, replacement in patterns:
        text, count = _apply(pattern, replacement, text)
        total += count
    return text, total


WINDOW = 300
REACH = 2000  # how far before the tail cut its window may start, to take in a key
KEEP_MAX = MAX_LINE // 2  # a kept piece stays under the line cap, so a second pass keeps it whole


def _cut_line(line: str, next_line: str = "") -> tuple[str, int]:
    """Cut a line over 4,000 characters, redacting around each cut first; return the line and the
    number of secrets redacted in what is kept.

    The kept head comes from redacting everything up to 300 characters past the head cut, the
    kept tail from redacting everything from `_tail_window_start` to the end of the line, after
    the opening of a value still open there (`_reopen`). Each
    window runs to the line's end on the kept side, so no kept token is cut by a window edge, and
    both are under about 4,000 characters, so the work stays bounded. If the head window ends
    inside a redacted value and no value end follows before the tail, that value fills the tail
    too, and the tail is dropped.
    """
    head, tail = cut_points(line, (MAX_LINE - 300) // 2, at_value_ends=True)
    kept_head, open_value = _kept_head(line[:head], line[head : head + WINDOW])
    if open_value and not VALUE_END.search(line, min(head + WINDOW, tail), tail + 1):
        tail = len(line)  # the redacted value runs on through the middle and the whole tail
    opener, start = _reopen(line, _tail_window_start(line, tail))
    rest = len(line) - _key_at_end(opener + line[start:], next_line)
    if rest < tail:
        rest = len(line)
    kept_tail = _kept_tail(opener + line[start:tail], line[tail:rest])
    if rest < len(line):
        # on a line of its own, so no value the kept tail leaves open (`<redacted>`, a quote)
        # takes it in
        kept_tail += f"\n{_lead(line)[:MAX_LEAD]}{line[rest:]}"
    found = max(0, kept_head.count(REDACTED) - line.count(REDACTED, 0, head))
    found += max(0, kept_tail.count(REDACTED) - line.count(REDACTED, tail))
    return join_cut(kept_head, tail - head, kept_tail, _lead(line)), found


_GAP_BEFORE = re.compile(r"[\s\"'][^\s\"']*\Z")
_KEY_BEFORE = re.compile(r"[\"']?[A-Za-z0-9_.-]{1,96}[\"']?\]?[ \t]*+(?:=>|:=|==|[=:])[ \t]*+\Z")


def _tail_window_start(line: str, tail: int) -> int:
    """Where the tail window starts: at the last whitespace or quote at least 300 characters
    before the tail cut, or before the key whose value follows it, about 2,000 characters back
    at most.

    A secret list (`API_KEYS=k1,…,k10`, `"api_keys": ["k1", …]`) runs on past any fixed edge, so
    a window starting inside it saw only elements, no key, and kept the later ones. A list with
    spaces is taken from its unclosed `[`.
    """
    lo = max(0, tail - REACH)
    edge = max(lo, tail - WINDOW)
    bracket = line.rfind("[", lo, edge)
    if bracket != -1 and line.find("]", bracket, edge) == -1:
        edge = bracket
    gap = _GAP_BEFORE.search(line, lo, edge)
    start = gap.start() if gap else edge
    for end in (edge, start):
        key = _KEY_BEFORE.search(line, max(0, end - 128), end)
        if key:
            start = min(start, key.start())
    return start


KEY_AT_END_NEXT = 256  # how much of the next line decides whether a key ending a line takes it
_KEY_AT_END = re.compile(
    rf"(?<![A-Za-z0-9_.-])(?=[A-Za-z0-9_.-]{{0,96}}?{_SECRET_HINT})"
    r"[A-Za-z0-9_.-]{1,96}[\"']?\]?[ \t]*+(?:=>|:=|==|[=:])[ \t]*+\Z",
    _FLAGS,
)


def _key_at_end(window: str, next_line: str) -> int:
    """How many characters at the end of the tail window are a key that `_KEY_VALUE` would pair
    with a value on the next line (`refresh_tokens:` and then `  ["a"]`), or 0. That key is kept
    out of the window: there a cookie or element opening would hide it with the rest of its
    line, and the final pass could no longer pair it with its value, as redacting the uncut text
    does, since `_KEY_VALUE` runs first."""
    key = _KEY_AT_END.search(window, max(0, len(window) - 160))
    if key is None:
        return 0
    probe = window + "\n" + next_line[:KEY_AT_END_NEXT]
    for match in _KEY_VALUE.finditer(probe):
        if match.end() > key.start():
            return len(window) - key.start() if match.start() == key.start() else 0
    return 0


def _reopen(line: str, start: int) -> tuple[str, int]:
    """Where the tail window starts and the text to put before it: the opening of a value that
    runs on across `start` (`API_KEYS=k`, `"api_keys": "`, `Cookie: x=`), so the window hides
    the rest of that value as redacting the whole line would, however far back its key is.

    The opening is found by reading the line from its start with the same patterns, which costs
    one linear pass. It stands in for the part of the value before the window, which the window
    never sees, and it is cut off again with the rest of the window's head (`_kept_tail`).
    """
    opener = "".join(
        found or "" for found in (_open_cookie(line, start), _open_element(line, start))
    ) + (_open_pair(line, start) or "")
    if not opener:
        return "", start
    before = line[:start]
    if (len(before) - len(before.rstrip("\\"))) % 2:
        start -= 1  # the window must not start on a character a backslash escapes (`\"`)
    return opener, start


OPENING_MAX = 256  # a key, separator or tag that runs across the window start, at most


def _opening(key: str, separator: str, value: str) -> str:
    return f"{key}{' '.join(separator.split())}{value[:1]}"


def _open_pair(text: str, start: int, depth: int = 0) -> str | None:
    """The opening of the secret value `_KEY_VALUE` takes across `start` in `text` (or of the key
    and separator it starts inside), or None. A value under a key that is no secret is searched
    for pairs inside it, as `_redact_inside` does, and hidden whole past `MAX_NESTING` levels."""
    for match in _KEY_VALUE.finditer(text):
        if match.end() > start:
            break
    else:
        return None
    key, separator, value = match.groups()
    if match.start() >= start:
        return None
    if match.start(3) >= start:
        return text[match.start() : start] if start - match.start() <= OPENING_MAX else None
    if _is_secret_key(key) and not _names_a_setting(match):
        return _opening(key, separator, value)
    if depth >= MAX_NESTING:
        # `_redact_inside` hides such a value whole if it holds a pair; any secret key does that
        return _opening("secret", separator, value) if _KEY_VALUE.search(value) else None
    return _open_pair(value, start - match.start(3), depth + 1)


def _open_element(line: str, start: int) -> str | None:
    """The opening tag of the secret element whose text runs across `start`, or the part of any
    tag `start` falls inside; None otherwise."""
    tag_start = line.rfind("<", 0, start)
    tag = _ELEMENT_OPEN.match(line, tag_start) if tag_start != -1 else None
    if tag is None:
        return None
    if tag.end() > start:
        return line[tag_start:start]
    return tag.group(0) if _is_secret_element(tag.group(1), tag.group(2)) else None


_COOKIE_NAME = re.compile(r"cookies?[\"']?[ \t]*+[:=]", _FLAGS)


def _open_cookie(line: str, start: int) -> str | None:
    """The opening of the cookie value `_COOKIE` takes across `start`, or None. A value without
    quotes runs to the end of the line. The cheap `_COOKIE_NAME` finds each header first, so the
    full pattern runs only near one."""
    pos = 0
    while (name := _COOKIE_NAME.search(line, pos, start + 64)) is not None:
        found = _COOKIE.search(line, max(pos, name.start() - 32), name.end())
        if found is None or found.start() >= start:
            pos = name.end()
            continue
        header = _COOKIE.match(line, found.start())
        if header is None or header.end() <= start:
            # a quoted cookie value that closes before `start`: the next header can only come
            # after it, as matches of `_COOKIE` never overlap
            pos = name.end() if header is None else max(header.end(), name.end())
            continue
        if header.start(2) >= start:
            return line[header.start() : start] if start - header.start() <= OPENING_MAX else None
        value = header.group(2)
        quote = value[:1] if value[:1] in ('"', "'") else ""
        # a plain header: its own name prefix (`SecretCookie:`) could read as a secret key
        return f"Cookie: {quote}x="
    return None


_SEGMENT = string.ascii_letters + string.digits + "_-"
_JWT_START = re.compile(_starting("eyJ"))


def _hide_cut_jwt(text: str) -> str:
    """A JWT the head window's far edge cut before its third segment (`eyJ…` of at least eight
    characters, a dot and a part at the very end), which the JWT pattern missed. Found from the
    end, so a run of `-eyJ-eyJ…` is read once."""
    before_dot = text.rstrip(_SEGMENT)
    if not before_dot.endswith("."):
        return text
    dot = len(before_dot) - 1
    start = _JWT_START.search(text, len(before_dot[:dot].rstrip(_SEGMENT)), dot)
    if start is None or dot - start.start() < 8:
        return text
    return text[: start.start()] + REDACTED


def _common_prefix(a: str, b: str) -> int:
    """How many characters `a` and `b` share at the start (a binary search over slices, so the
    comparing runs in C)."""
    low, high = 0, min(len(a), len(b))
    while low < high:
        middle = (low + high + 1) // 2
        if a[:middle] == b[:middle]:
            low = middle
        else:
            high = middle - 1
    return low


def _common_suffix(a: str, b: str) -> int:
    low, high = 0, min(len(a), len(b))
    while low < high:
        middle = (low + high + 1) // 2
        if a[len(a) - middle :] == b[len(b) - middle :]:
            low = middle
        else:
            high = middle - 1
    return low


def _kept_head(head: str, after: str) -> tuple[str, bool]:
    """The part of `redact(head + after)` that stands for `head`, never ending inside a
    `<redacted>`, and whether the window ended inside a redacted value. Where the mapping is
    unsure it keeps less, and any piece of the redacted window is safe to keep; text before the
    first change is `head`'s own. Text of `head` it drops unmarked is marked `<redacted>`, so
    the count shows that something went."""
    window = head + after
    new = _hide_cut_jwt(_redact_all(window, cut_end=True)[0])
    if new.endswith(after):  # `after` came through unchanged
        end = len(new) - len(after)
    elif new.startswith(head):  # `head` came through unchanged
        end = len(head)
    else:
        end = min(len(head), len(new) - len(after))
    end = max(0, end, min(_common_prefix(new, window), len(head)))
    end = min(end, KEEP_MAX)
    marker = new.rfind(REDACTED, max(0, end - len(REDACTED) + 1), end + len(REDACTED) - 1)
    if marker != -1:
        end = marker + len(REDACTED)
    kept = new[:end]
    if len(kept) < len(head) and head.startswith(kept):
        kept += REDACTED
    return kept, new.endswith(REDACTED)


def _kept_tail(before: str, tail: str) -> str:
    """The part of `redact(before + tail)` that stands for `tail`; see `_kept_head`."""
    window = before + tail
    new = _redact_all(window, in_cut=True)[0]
    if new.startswith(before):
        start = len(before)
    elif new.endswith(tail):
        start = len(new) - len(tail)
    else:
        start = max(len(before), len(new) - len(tail))
    same_end = min(_common_suffix(new, window), len(tail))
    start = min(start, len(new) - same_end)
    start = min(len(new), max(start, len(new) - KEEP_MAX))
    marker = new.find(REDACTED, max(0, start - len(REDACTED) + 1), start + len(REDACTED) - 1)
    if marker != -1:
        start = marker
    kept = new[start:]
    if len(kept) < len(tail) and tail.endswith(kept):
        kept = REDACTED + kept
    return kept


def redact_with_count(text: str) -> tuple[str, int]:
    """Redact secrets in free text; return the new text and how many values were replaced.

    Lines over 4,000 characters are cut in the middle first (after redacting around each cut, so
    a key and value the cut would separate are caught), which bounds the running time. The count
    is what the guard log records: one per secret value in the returned text. Running it again on
    its own output changes nothing and counts 0.
    """
    text = normalise_newlines(text)
    found = 0
    if len(text) > MAX_LINE:
        lines = text.split("\n")
        for index, line in enumerate(lines):
            if len(line) > MAX_LINE:
                following = lines[index + 1] if index + 1 < len(lines) else ""
                lines[index], count = _cut_line(line, following)
                found += count
        text = "\n".join(lines)
    try:
        text, total = _redact_all(text)
    finally:
        _json_strings.cache_clear()  # it holds the unredacted input
    return text, total + found


def redact_text(text: str) -> str:
    """`redact_with_count` without the count."""
    return redact_with_count(text)[0]


def _hides_whole_value(key: str) -> bool:
    """Whether an approval shows `<redacted>` for the whole value under `key`: it names a secret
    and no place (`_APPROVAL_PLACES`, or `dsn` as its last word: `SECRET_DSN` is shown,
    `DSN_PASSWORD` hidden). Fails safe by showing: a value that is shown still goes through
    redaction."""
    words = {w.lower() for w in WORDS.findall(key)}
    return _is_secret_key(key) and not (words & _APPROVAL_PLACES or _is_dsn_key(key))


def redacted_step(step: PlanStep) -> PlanStep:
    """The step as a person approving it sees it: every param redacted, nothing hidden.

    A `coolify.set_env` value under a key that names a secret (`STRIPE_KEY`, `DB_PASSWORD`) and
    no place (`_hides_whole_value`: `AUTH0_DOMAIN` is shown) is `<redacted>` whole, spaces, quotes
    and line breaks included; any other is redacted by its own patterns, so a password inside a
    URL value is cut out while the host and the rest stay visible. A DSN is a place: under a key
    ending in `DSN` its userinfo is hidden too (`https://<redacted>@o0.ingest.sentry.io/1`), the
    host shown. Danny's decision (#38): an
    explicit yes must see what it approves, since planted text could steer the model to a value
    such as an attacker's host."""
    params = {k: redact_text(v) for k, v in step.params.items()}
    if step.action == "coolify.set_env" and {"key", "value"} <= step.params.keys():
        key = step.params["key"]
        # A shown value is redacted on its own, so the key cannot hide it (`SECRET_HOST`).
        if _hides_whole_value(key):
            params["value"] = REDACTED
        elif _is_dsn_key(key):
            params["value"] = _hide_dsn_user(params["value"])
    return step.model_copy(update={"params": params})


def step_head(number: int, step: PlanStep) -> str:
    """A step's number, action, target and host as the Telegram approval heads its line, before
    redaction (`notify/format._step_line`)."""
    return f"{number}. {step.action} {step.target} on {step.host_id}"


def hides_head(number: int, step: PlanStep) -> bool:
    """Whether the Telegram line would show step `number`'s head other than it runs: redacted
    (a target that reads like a token), with a presentation selector dropped, or holding a control
    character (`_shown_otherwise`)."""
    return _shown_otherwise(step_head(number, step))


_CONTROL_CHAR = re.compile(CONTROL_CHARS)


def _shown_otherwise(text: str) -> bool:
    """Whether a display would show `text` other than it is: redacted, with a presentation
    selector dropped, or holding a control character (C0 but line feed and tab, DEL, C1), which
    Telegram shows as a line break and the dashboard as the browser does."""
    return (
        redact_text(text) != text
        or any(c in text for c in PRESENTATION_SELECTORS)
        or _CONTROL_CHAR.search(text) is not None
    )


MAX_PARAM_LINES = 30
# Three line breaks with only blanks between them, or 16 blanks of any kind in a row: enough to
# push the rest of a param out of sight on the dashboard, which keeps a param's line breaks.
_PADDING = re.compile(r"\n(?:[^\S\n]*\n){2}|\s{16}")


def padded(text: str) -> bool:
    """Whether `text` is padded so a reader could miss what follows: more than two line breaks
    in a row (a line of blanks counts as empty), a run of 16 or more blanks of any kind (NBSP,
    U+3000 and tabs included), or more than `MAX_PARAM_LINES` lines."""
    return _PADDING.search(text) is not None or text.count("\n") >= MAX_PARAM_LINES


def pads_step(number: int, step: PlanStep) -> bool:
    """Whether step `number`'s head or any param (`key=value`, as the dashboard shows it) is
    `padded`."""
    shown = [step_head(number, step), *(f"{k}={v}" for k, v in step.params.items())]
    return any(padded(text) for text in shown)


def params_line(step: PlanStep) -> str:
    """A step's params on one line, `tool` first, before redaction: what the Telegram approval
    shows of a plugin step (`notify/format._step_line`)."""
    params = sorted(step.params.items(), key=lambda item: item[0] != "tool")
    return ", ".join(f"{k}={v}" for k, v in params)


def hides_params(step: PlanStep) -> bool:
    """Whether a person approving `step` would see any of its params other than they run: changed
    by redaction on the dashboard (`redacted_step`) or in the Telegram line (`params_line`,
    redacted as a whole, where one param's text can hide the next), or holding a presentation
    selector or a control character (`_shown_otherwise`)."""
    return redacted_step(step).params != step.params or _shown_otherwise(params_line(step))


def redacted_proposal(proposal: Proposal) -> Proposal:
    """The plan as the dashboard, the event stream and a chat show it (`redacted_step`); Execute
    alone reads the real one."""
    return proposal.model_copy(
        update={
            "steps": [redacted_step(s) for s in proposal.steps],
            "refused_steps": [redacted_step(s) for s in proposal.refused_steps],
        }
    )
