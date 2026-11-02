"""Keep secrets away from the model, the logs and the database."""

import re
from collections.abc import Callable

REDACTED = "<redacted>"


def redact_env(env: list[str]) -> list[str]:
    """`KEY=value` becomes `KEY=<redacted>` for every entry; a bare `KEY` stays as it is."""
    return [f"{item.partition('=')[0]}={REDACTED}" if "=" in item else item for item in env]


_FLAGS = re.IGNORECASE
_KEYWORDS = r"(?:password|passwd|secret|token|api_key|apikey)"

# (pattern, replacement). A replacement is a template or a function of the match.
# Values never start with `<`, so the pattern cannot match its own output (idempotent).
_PATTERNS: list[tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]]] = [
    # -----BEGIN … PRIVATE KEY----- to its END line, or to the end of the text when cut off
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)",
            re.DOTALL,
        ),
        REDACTED,
    ),
    # Authorization: <anything>, whatever the scheme
    (
        re.compile(
            r"(authorization[\"']?\s*[:=]\s*[\"']?)"
            r"(?!(?:(?:bearer|basic|token|digest)\s+)?<redacted>)"
            r"((?:bearer|basic|token|digest)\s+)?[^\s\"'<]+",
            _FLAGS,
        ),
        rf"\1\2{REDACTED}",
    ),
    # a bare "Bearer xyz" / "Basic xyz": must look like a credential, not prose
    (
        re.compile(
            r"\b(bearer|basic)(\s+)(?=[A-Za-z0-9._~+/=-]{8,}(?![^\s]))"
            r"(?=[^\s]*[0-9._~+/=-])[^\s]+",
            _FLAGS,
        ),
        rf"\1\2{REDACTED}",
    ),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), REDACTED),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), REDACTED),
    (re.compile(r"\bsk-(?:or-)?[A-Za-z0-9_-]{16,}"), REDACTED),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"),
        REDACTED,
    ),
    # scheme://user:password@host: keep the user
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://[^\s:/@<]*:)[^\s@/<]+(@)", _FLAGS), rf"\1{REDACTED}\2"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), REDACTED),
    (re.compile(r"(?<![0-9])\d{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])"), REDACTED),
    # password=x, "password": "x y", password: x, DB_PASSWORD=x
    (
        re.compile(
            rf"((?<![A-Za-z0-9])[A-Za-z0-9_.-]*{_KEYWORDS}(?![A-Za-z0-9])[\"']?\s*[=:]\s*)"
            r"(?:\"[^\"\n<]*\"|'[^'\n<]*'|[^\s\"',;&<>]+)",
            _FLAGS,
        ),
        lambda m: _keep_quotes(m),
    ),
]


def _keep_quotes(match: re.Match[str]) -> str:
    value = match.group(0)[len(match.group(1)) :]
    quote = value[0] if value[0] in "\"'" else ""
    return f"{match.group(1)}{quote}{REDACTED}{quote}"


def redact_with_count(text: str) -> tuple[str, int]:
    """Redact secrets in free text; return the new text and how many values were replaced.

    The count is what the guard log records. Running it again on its own output changes nothing
    and counts 0.
    """
    total = 0
    for pattern, replacement in _PATTERNS:
        text, count = pattern.subn(replacement, text)
        total += count
    return text, total


def redact_text(text: str) -> str:
    """`redact_with_count` without the count."""
    return redact_with_count(text)[0]
