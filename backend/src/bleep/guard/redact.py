"""Keep secrets away from the model, the logs and the database.

Log content controls the input of these patterns, so each one is written to run in linear time:
every quantifier next to an overlapping class is bounded or anchored by a lookbehind, and
`redact_with_count` first cuts any line over 4,000 characters.
"""

import re
from collections.abc import Callable

from bleep.guard.sanitize import cap_lines

REDACTED = "<redacted>"


def redact_env(env: list[str]) -> list[str]:
    """`KEY=value` becomes `KEY=<redacted>` for every entry; a bare `KEY` stays as it is."""
    return [f"{item.partition('=')[0]}={REDACTED}" if "=" in item else item for item in env]


_FLAGS = re.IGNORECASE
_SCHEME = r"(?:bearer|basic|token|digest)"

# --- key=value: the key is split into words and judged in Python, the value is fail-safe ---------
_WORDS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")  # SECRET_KEY, secretKey, APIKey
_SECRET_WORDS = {
    "password", "passwd", "pass", "pwd", "passphrase", "secret", "secrets", "token", "credential",
    "credentials", "auth", "apikey",
}  # fmt: skip
_SECRET_PARTS = ("password", "passwd", "apikey", "secret")  # PGPASSWORD, awssecretaccesskey
_KEY_PREFIXES = {  # `<prefix>_key` is a secret too
    "api", "access", "private", "encryption", "signing", "master", "ssh", "license", "session",
    "jwt", "crypto", "auth",
}  # fmt: skip
# Words that mark a setting about a secret rather than the secret itself: token_count, secret_name
_NOT_A_SECRET = {
    "count", "reset", "type", "name", "file", "path", "url", "uri", "length", "expires", "expiry",
    "expiration", "ttl", "endpoint", "header", "prefix", "limit", "max", "min", "mode", "size",
    "bytes", "format", "algorithm", "alg", "kind", "provider", "status", "required", "enabled",
    "disabled", "policy", "dir", "folder",
}  # fmt: skip
# Values that are never secrets, compared case-insensitively against the whole value.
_PLAIN_VALUES = {
    "true", "false", "yes", "no", "on", "off", "none", "null", "nil", "empty", "unset", "required",
    "enabled", "disabled", "expired", "invalid", "missing", "ok", "failed", "***", "<redacted>",
}  # fmt: skip
_REFERENCE = re.compile(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?")
_SMALL_INT = re.compile(r"[0-9]{1,6}")
_LOOKS_LIKE_PAIR = re.compile(r"[A-Za-z_][\w.-]*=[^=\s]")


def _is_secret_key(key: str) -> bool:
    words = [w.lower() for w in _WORDS.findall(key)]
    hit = None
    for index, word in enumerate(words):
        if (
            word in _SECRET_WORDS
            or any(part in word for part in _SECRET_PARTS)
            or (len(word) > 5 and word.endswith("token"))
            or (word == "key" and index > 0 and words[index - 1] in _KEY_PREFIXES)
        ):
            hit = hit or word
    if hit is None:
        return False
    if any(w in _NOT_A_SECRET for w in words):
        return False
    return not ("id" in words and hit in ("token", "session"))


def _is_plain(value: str) -> bool:
    value = value.rstrip(",;})]").lower()
    return (
        value in _PLAIN_VALUES
        or _SMALL_INT.fullmatch(value) is not None
        or _REFERENCE.fullmatch(value) is not None
    )


def _redact_pair(match: re.Match[str]) -> str:
    key, separator, value = match.group(1), match.group(2), match.group(3)
    if not _is_secret_key(key):
        return match.group(0)
    quote = value[0] if value[0] in "\"'" else ""
    inner = value[1:-1] if quote and len(value) > 1 and value.endswith(quote) else value
    if quote and inner == value:
        inner = value[1:]
    rest = match.string[match.start(3) :]
    if _is_plain(inner) or re.match(r"not set\b", rest, _FLAGS):
        return match.group(0)
    if not quote and ":" in separator and _LOOKS_LIKE_PAIR.match(value):
        return match.group(0)  # `credentials: user=bob` is a message, not a value
    if value[0] in "|>":
        return f"{key}{separator}{REDACTED}"
    if quote:
        closed = len(value) > 1 and value.endswith(quote)
        return f"{key}{separator}{quote}{REDACTED}{quote if closed else ''}"
    return f"{key}{separator}{REDACTED}"


_COOKIE_ATTRS = {"path", "domain", "expires", "max-age", "samesite", "secure", "httponly"}


def _redact_cookie(match: re.Match[str]) -> str:
    parts = []
    for part in match.group(2).split(";"):
        name, eq, value = part.partition("=")
        if eq and name.strip().lower() not in _COOKIE_ATTRS and value.strip() != REDACTED:
            part = f"{name}={REDACTED}"
        parts.append(part)
    return match.group(1) + ";".join(parts)


Replacement = str | Callable[[re.Match[str]], str]
_PATTERNS: list[tuple[re.Pattern[str], Replacement]] = [
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
    (re.compile(r"((?:set-)?cookie[ \t]*:[ \t]*)([^\n]*)", _FLAGS), _redact_cookie),
    # a bare "Bearer xyz" / "Basic xyz" must look like a credential, not prose
    (
        re.compile(
            r"\b(bearer|basic)(\s+[\"']?)(?=[A-Za-z0-9._~+/=-]{8,}(?![^\s\"']))"
            r"(?=[^\s\"']*[0-9._~+/=-])[^\s\"']+",
            _FLAGS,
        ),
        rf"\1\2{REDACTED}",
    ),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), REDACTED),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), REDACTED),
    (re.compile(r"\bsk-(?:or-)?[A-Za-z0-9_-]{16,}"), REDACTED),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"), REDACTED),
    # scheme://user:password@host: keep the user. The password may hold `@` and `/`; it ends at
    # the first `@` that is followed by a host and then `/`, `?`, `#`, whitespace or the end.
    (
        re.compile(
            r"(\b[a-z][a-z0-9+.-]{0,31}://[^\s:/@]{0,64}:)(?!<redacted>@)\S{1,512}?(@)"
            r"(?=[A-Za-z0-9._\[\]:-]+(?:[/?#\s]|$))",
            _FLAGS,
        ),
        rf"\1{REDACTED}\2",
    ),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16,}\b"), REDACTED),
    (re.compile(r"(?<![0-9])\d{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])"), REDACTED),
    # crypt hashes: htpasswd/basic-auth ($apr1$, $2y$, $5$, $6$, $y$, $argon2id$)
    (re.compile(r"\$(?:apr1|2[aby]|5|6|y|argon2(?:id|i|d))\$[^\s\"']+"), REDACTED),
    # password=x, "password": "x y", password: x, SECRET_KEY_BASE=x, X-Api-Key: x. The key is
    # judged by `_is_secret_key`; the cheap lookahead keeps Python out of unrelated pairs. An
    # unquoted value runs to whitespace (so it fails safe on `&`, `,`, quotes); a YAML block
    # scalar (`|`, `>`) takes its indented lines (at most 20).
    (
        re.compile(
            r"(?<![A-Za-z0-9_.-])"
            r"(?=[A-Za-z0-9_.-]{0,96}?(?:pass|pwd|secret|token|credential|auth|key))"
            r"([A-Za-z0-9_.-]{1,96})"
            r"([\"']?[ \t]*+[=:][ \t]*+(?:(?:\n[ \t]*+){1,3}(?<=[ \t]))?)"
            r"(\"(?:[^\"\\\n]|\\.)*+\"?"
            r"|'(?:[^'\\\n]|\\.)*+'?"
            r"|[|>][-+0-9]*[ \t]*+\n(?:[ \t]++[^\n]*+\n?+){1,20}"
            r"|[^\s\"'][^\s]*+)",
            _FLAGS,
        ),
        _redact_pair,
    ),
]


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
    """`pattern.sub`, counting only the matches whose text actually changed."""
    changed = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal changed
        out = replacement(match) if callable(replacement) else match.expand(replacement)
        changed += out != match.group(0)
        return out

    return pattern.sub(replace, text), changed


def redact_with_count(text: str) -> tuple[str, int]:
    """Redact secrets in free text; return the new text and how many values were replaced.

    Lines over 4,000 characters are cut in the middle first, which bounds the running time. The
    count is what the guard log records. Running it again on its own output changes nothing and
    counts 0.
    """
    text = cap_lines(text)
    text, total = _redact_keys(text)
    for pattern, replacement in _PATTERNS:
        text, count = _apply(pattern, replacement, text)
        total += count
    return text, total


def redact_text(text: str) -> str:
    """`redact_with_count` without the count."""
    return redact_with_count(text)[0]
