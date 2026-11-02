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
_KEYWORDS = (
    r"(?:password|passwd|pass|pwd|secret|token|api[-_]?key|access[-_]?key|private[-_]?key"
    r"|client[-_]?secret|credentials|auth)"
)
# `SECRET_KEY`, `AWS_SECRET_ACCESS_KEY`, `GITHUB_TOKEN_VALUE`; not `token_count`, `password_reset`
_SUFFIX = r"(?:[_.-](?:access|key|value|string|hash|token))*"
_SCHEME = r"(?:bearer|basic|token|digest)"

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
    # scheme://user:password@host: keep the user; the password runs to the last `@` before the host
    (
        re.compile(
            r"(\b[a-z][a-z0-9+.-]{0,31}://[^\s:/@]{0,128}:)(?!<redacted>@)[^\s/]+(@)",
            _FLAGS,
        ),
        rf"\1{REDACTED}\2",
    ),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16,}\b"), REDACTED),
    (re.compile(r"(?<![0-9])\d{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])"), REDACTED),
    # crypt hashes: htpasswd/basic-auth ($apr1$, $2y$, $5$, $6$, $y$)
    (re.compile(r"\$(?:apr1|2[aby]|5|6|y)\$[^\s\"']+"), REDACTED),
    # password=x, "password": "x y", password: x, DB_PASSWORD=x, X-Api-Key: x
    (
        re.compile(
            r"((?<![A-Za-z0-9_.-])[A-Za-z0-9_.-]{0,64}?"
            rf"{_KEYWORDS}{_SUFFIX}(?![A-Za-z0-9])[\"']?\s*[=:]\s*)"
            r"(?![\"']?<redacted>)"
            r"(\"(?:[^\"\\\n]|\\.)*\"?|'(?:[^'\\\n]|\\.)*'?|[^\s\"']+)",
            _FLAGS,
        ),
        lambda m: _keep_quotes(m),
    ),
]


def _keep_quotes(match: re.Match[str]) -> str:
    value = match.group(2)
    if value[0] in "\"'":
        closed = len(value) > 1 and value.endswith(value[0])
        return f"{match.group(1)}{value[0]}{REDACTED}{value[0] if closed else ''}"
    return f"{match.group(1)}{REDACTED}"


_PEM_BEGIN = re.compile(r"-----BEGIN [A-Z ]{0,30}PRIVATE KEY-----")
_PEM_END = re.compile(r"-----END [A-Z ]{0,30}PRIVATE KEY-----")
_PEM_BODY = re.compile(r"[A-Za-z0-9+/]{4,}={0,2}|(?:Proc-Type|DEK-Info):.*")
_B64_ANY = re.compile(r"[A-Za-z0-9+/]{60}")
_B64_LINE = re.compile(r"(?=[^A-Z]*[A-Z])(?=[^a-z]*[a-z])[A-Za-z0-9+/]{60,80}={0,2}")  # not all hex
PEM_MAX_LINES = 100


def _is_b64(line: str) -> bool:
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
            while j < min(i + 1 + PEM_MAX_LINES, len(lines)) and _PEM_BODY.fullmatch(lines[j]):
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


def redact_with_count(text: str) -> tuple[str, int]:
    """Redact secrets in free text; return the new text and how many values were replaced.

    Lines over 4,000 characters are cut in the middle first, which bounds the running time. The
    count is what the guard log records. Running it again on its own output changes nothing and
    counts 0.
    """
    text = cap_lines(text)
    text, total = _redact_keys(text)
    for pattern, replacement in _PATTERNS:
        text, count = pattern.subn(replacement, text)
        total += count
    return text, total


def redact_text(text: str) -> str:
    """`redact_with_count` without the count."""
    return redact_with_count(text)[0]
