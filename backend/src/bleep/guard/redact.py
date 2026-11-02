"""Keep secrets away from the model, the logs and the database.

Redaction is best-effort defence in depth. What it must never do is take unbounded time, so the
guard first cuts the input (see `guard.pipeline`), `redact_with_count` cuts every line over 4,000
characters, and each pattern below is written to run in linear time on what is left.
"""

import re
from collections.abc import Callable
from functools import lru_cache

from bleep.guard.sanitize import MAX_LINE, cut_points, join_cut, normalise_newlines

REDACTED = "<redacted>"


def redact_env(env: list[str]) -> list[str]:
    """`KEY=value` becomes `KEY=<redacted>` for every entry; a bare `KEY` stays as it is."""
    return [f"{item.partition('=')[0]}={REDACTED}" if "=" in item else item for item in env]


_FLAGS = re.IGNORECASE
_SCHEME = r"(?:bearer|basic|token|digest)"

# --- key names: split into words and judged in Python ----------------------------------------
_WORDS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")  # SECRET_KEY, secretKey, APIKey
_SECRET_WORDS = {
    "password", "passwd", "pass", "pwd", "passphrase", "secret", "secrets", "token", "credential",
    "credentials", "auth", "apikey", "pw", "secretkey", "privatekey", "accesskey", "accesstoken",
    "authkey",
}  # fmt: skip
_SECRET_SUFFIXES = ("password", "passwd", "apikey", "secret", "authkey")  # PGPASSWORD, TS_AUTHKEY
# Words that keep their meaning with a trailing "s" (PASSWORDS, API_KEYS). Not "tokens": that is
# almost always a count (max_tokens, prompt_tokens).
_PLURALS = {
    "password", "passwd", "pwd", "passphrase", "credential", "apikey", "key", "authkey",
    "secretkey", "privatekey", "accesskey",
}  # fmt: skip
_KEY_PREFIXES = {  # `<prefix>_key` is a secret too
    "api", "access", "private", "encryption", "signing", "master", "ssh", "license", "session",
    "jwt", "crypto", "auth", "hmac", "aes", "webhook", "app", "stripe", "openai", "anthropic",
    "sendgrid", "mailgun", "admin", "client", "shared", "service", "deploy", "root",
}  # fmt: skip
_ENV_KEY = re.compile(r"[A-Z0-9_]+_KEYS?")  # APP_KEY, STRIPE_KEY, PUSHER_APP_KEY; not cache_key
# A word AFTER the last secret word that marks a setting about the secret: token_count,
# password_reset, auth_method. A word before it changes nothing (reset_password_token is a token).
_NOT_A_SECRET = {
    "count", "reset", "type", "name", "file", "path", "url", "uri", "length", "expires", "expiry",
    "expiration", "ttl", "endpoint", "header", "prefix", "limit", "max", "min", "mode", "size",
    "bytes", "format", "algorithm", "alg", "kind", "provider", "status", "required", "enabled",
    "disabled", "policy", "dir", "folder", "method", "mechanism", "backend", "source", "audience",
    "scheme", "stdin",
}  # fmt: skip
# Values that are never secrets, compared case-insensitively against the whole value.
_PLAIN_VALUES = {
    "true", "false", "yes", "no", "on", "off", "none", "null", "nil", "empty", "unset", "required",
    "enabled", "disabled", "expired", "invalid", "missing", "ok", "failed", "***", "<redacted>",
}  # fmt: skip
# `${VAR}`, `${env:VAR}`, `${secrets.db}` and `$UPPER_VAR`; a default (`${VAR:-x}`, `${VAR:=x}`,
# `${VAR-x}`) holds a literal value and is not a reference.
_REFERENCE = re.compile(
    r"\$\{[A-Za-z_][A-Za-z0-9_.]*(?::[A-Za-z_][A-Za-z0-9_.]*)?\}|\$[A-Z_][A-Z0-9_]*"
)
_SMALL_INT = re.compile(r"[0-9]{1,6}")
_LOOKS_LIKE_PAIR = re.compile(r"[A-Za-z_][\w.-]*=[^=\s]")
_PAIR_FOLLOWS = re.compile(r"[^\S\n]+[A-Za-z_][\w.-]*=\S")
_NOT_SET = re.compile(r"not set\b", re.IGNORECASE)
_BLOCK_HEADER = re.compile(r"[|>][-+0-9]*")


@lru_cache(maxsize=4096)
def _is_secret_key(key: str) -> bool:
    if key in ("PWD", "OLDPWD"):  # the shell's working directory
        return False
    if _ENV_KEY.fullmatch(key):
        return True
    words = [_singular(w.lower()) for w in _WORDS.findall(key)]
    last = -1
    for index, word in enumerate(words):
        if (
            word in _SECRET_WORDS
            or word.endswith(_SECRET_SUFFIXES)
            or (len(word) > 5 and word.endswith("token"))
            or (word == "key" and index > 0 and words[index - 1] in _KEY_PREFIXES)
        ):
            last = index
    if last < 0:
        return False
    after = words[last + 1 :]
    if any(w in _NOT_A_SECRET for w in after):
        return False
    return not ("id" in after and words[last] in ("token", "session"))


def _singular(word: str) -> str:
    if word.endswith("s") and (word[:-1] in _PLURALS or word[:-1].endswith(_SECRET_SUFFIXES)):
        return word[:-1]
    return word


def _is_plain(value: str) -> bool:
    if _REFERENCE.fullmatch(value.rstrip(",;")):
        return True
    value = value.rstrip(",;})]").lower()
    return value in _PLAIN_VALUES or _SMALL_INT.fullmatch(value) is not None


def _redact_pair(match: re.Match[str], nested: bool = False) -> str:
    key, separator, value = match.group(1), match.group(2), match.group(3)
    if not _is_secret_key(key) or _BLOCK_HEADER.fullmatch(value):
        return match.group(0)
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
        not nested
        and not quote
        and key.lower() in ("credentials", "auth")
        and _LOOKS_LIKE_PAIR.match(value)
        and _PAIR_FOLLOWS.match(match.string, match.end(3))
    ):
        # `invalid credentials: user=bob retries=3` is a message; its own pairs are still
        # judged, so `auth: token=x user=bob` hides x
        return f"{key}{separator}{_KEY_VALUE.sub(_redact_nested, value)}"
    if quote:
        return f"{key}{separator}{quote}{REDACTED}{quote if closed else ''}"
    return f"{key}{separator}{REDACTED}"


def _redact_nested(match: re.Match[str]) -> str:
    return _redact_pair(match, nested=True)


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


# password=x, "password": "x y", password: x, [password] => x, SECRET_KEY_BASE=x, X-Api-Key: x.
# The key is judged by `_is_secret_key`; the cheap lookahead keeps Python out of unrelated pairs.
# An unquoted value runs to whitespace (so it fails safe on `&`, `,`, quotes) and never starts
# with a cut note.
_KEY_VALUE = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    r"(?=[A-Za-z0-9_.-]{0,96}?(?:pass|pw|secret|token|credential|auth|key))"
    r"([A-Za-z0-9_.-]{1,96})"
    r"([\"']?\]?[ \t]*+(?:=>|:=|==|[=:])[ \t]*+(?:(?:\n[ \t]*+){1,8}(?<=[ \t]))?)"
    r"(?!\[… )"
    r"(\"(?:[^\"\\\n]|\\.)*+\"?|'(?:[^'\\\n]|\\.)*+'?|[^\s\"'][^\s]*+)",
    _FLAGS,
)


def _redact_element(match: re.Match[str]) -> str:
    """`<password>x</password>`: an XML element named like a secret."""
    if not _is_secret_key(match.group(1)) or _is_plain(match.group(2)):
        return match.group(0)
    return f"<{match.group(1)}>{REDACTED}{match.group(3)}"


Replacement = str | Callable[[re.Match[str]], str]
_PATTERNS: list[tuple[re.Pattern[str], Replacement]] = [
    # runs first so a value holding a token (`password=ghp_...!tail`) is one redaction
    (_KEY_VALUE, _redact_pair),
    (
        re.compile(
            r"<(?=[A-Za-z0-9_.:-]{0,64}?(?:pass|pw|secret|token|credential|auth|key))"
            r"([A-Za-z0-9_.:-]{1,64})>([^<\n]{1,512}+)(</\1>)",
            _FLAGS,
        ),
        _redact_element,
    ),
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
    # Cookie and Set-Cookie, as a header, JSON or logfmt (request_Cookie, "cookie": "..."): the
    # cookie names stay, their values go
    (
        re.compile(
            r"((?<![A-Za-z0-9])[A-Za-z0-9_-]{0,32}cookies?[\"']?[ \t]*+[:=][ \t]*+)"
            r"(\"(?:[^\"\\\n]|\\.)*+\"?|'(?:[^'\\\n]|\\.)*+'?|[^\n]*)",
            _FLAGS,
        ),
        _redact_cookie,
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
    # scheme://user:password@host: keep the user. The password may hold `@` and `/`; it ends at
    # the first `@` that is followed by a host and then `/`, `?`, `#`, whitespace or the end. The
    # lookbehind lets only the start of a scheme begin a match, so starts cannot overlap.
    (
        re.compile(
            r"((?<![a-z0-9+.-])[a-z][a-z0-9+.-]{0,31}://[^\s:/@]{0,64}:)(?!<redacted>@)\S{1,128}?(@)"
            r"(?=[A-Za-z0-9._\[\]:-]+(?:[/?#\s]|$))",
            _FLAGS,
        ),
        rf"\1{REDACTED}\2",
    ),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16,}\b"), REDACTED),
    (re.compile(r"(?<![0-9])\d{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])"), REDACTED),
    # crypt hashes: htpasswd/basic-auth ($apr1$, $2y$, $5$, $6$, $y$, $argon2id$)
    (re.compile(r"\$(?:apr1|2[aby]|5|6|y|argon2(?:id|i|d))\$[^\s\"']+"), REDACTED),
]

BLOCK_MAX_LINES = 200
# A log prefix before the YAML: a timestamp, a Compose `web-1  | ` or a `[tag] `. It is skipped
# before measuring indentation, so `web-1  |   x` is indented by 2.
_LOG_PREFIX = re.compile(
    r"(?:\d{4}-\d\d-\d\d[T ][\d:.,]{1,20}Z?[ \t]"
    r"|[A-Za-z0-9_.-]{1,64}[ \t]*\|[ \t]"
    r"|\[[^\]\n]{0,40}\][ \t])?"
)
_KEY_REVERSED = re.compile(r"[A-Za-z0-9_.-]{1,96}")


def _lead(line: str) -> str:
    """The log prefix and indentation of `line`."""
    rest = line[_LOG_PREFIX.match(line).end() :]
    return line[: len(line) - len(rest.lstrip(" \t"))]


def _block_indent(line: str) -> int | None:
    """If `line` ends in `<secret key>: |` (or `>`, any prefix before it), the indentation a
    block line must exceed; otherwise None. Read from the end so it costs one pass."""
    if len(line) > 400 or ("|" not in line and ">" not in line):
        return None
    text = line.split(" #", 1)[0].rstrip().rstrip("-+0123456789")
    if not text.endswith(("|", ">")):
        return None
    text = text[:-1].rstrip(" \t")
    if not text.endswith(":"):
        return None
    text = text[:-1].rstrip(" \t").removesuffix('"').removesuffix("'")
    key = _KEY_REVERSED.match(text[::-1][:96])
    if key is None or not _is_secret_key(key.group(0)[::-1]):
        return None
    before = text[: len(text) - key.end()]
    if before and not before[-1].isspace() and before[-1] not in "\"'":
        return None
    return len(_lead(line)) - _LOG_PREFIX.match(line).end()


def _redact_blocks(text: str) -> tuple[str, int]:
    """YAML block scalars under a secret key (`password: |`): every line of the block, blank lines
    kept, up to the first line that is not indented deeper than the key (at most 200 lines)."""
    if "|" not in text and ">" not in text:
        return text, 0
    lines, count = text.split("\n"), 0
    for i, line in enumerate(lines):
        indent = _block_indent(line)
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
    """`pattern.sub`, counting only the matches whose text actually changed."""
    changed = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal changed
        out = replacement(match) if callable(replacement) else match.expand(replacement)
        changed += out != match.group(0)
        return out

    return pattern.sub(replace, text), changed


def _redact_all(text: str) -> tuple[str, int]:
    text, total = _redact_blocks(text)
    text, count = _redact_keys(text)
    total += count
    for pattern, replacement in _PATTERNS:
        text, count = _apply(pattern, replacement, text)
        total += count
    return text, total


WINDOW = 300
KEEP_MAX = MAX_LINE // 2  # a kept piece stays under the line cap, so a second pass keeps it whole


def _cut_line(line: str) -> tuple[str, int]:
    """Cut a line over 4,000 characters, redacting around each cut first; return the line and the
    number of secrets redacted in what is kept.

    The kept head comes from redacting everything up to 300 characters past the head cut, the
    kept tail from redacting everything from 300 characters before the tail cut to the end of the
    line. Each window runs to the line's end on the kept side, so no kept token is cut by a window
    edge, and both are under about 2,200 characters, so the work stays bounded.
    """
    head, tail = cut_points(line, (MAX_LINE - 300) // 2, whole_words=True)
    kept_head = _kept_head(line[:head], line[head : head + WINDOW])
    kept_tail = _kept_tail(line[max(0, tail - WINDOW) : tail], line[tail:])
    found = max(0, kept_head.count(REDACTED) - line.count(REDACTED, 0, head))
    found += max(0, kept_tail.count(REDACTED) - line.count(REDACTED, tail))
    return join_cut(kept_head, tail - head, kept_tail, _lead(line)), found


def _kept_head(head: str, after: str) -> str:
    """The part of `redact(head + after)` that stands for `head`, never ending inside a
    `<redacted>`. Where the mapping is unsure it keeps less, and any piece of the redacted window
    is safe to keep."""
    new = _redact_all(head + after)[0]
    if new.endswith(after):  # `after` came through unchanged
        end = len(new) - len(after)
    elif new.startswith(head):  # `head` came through unchanged
        end = len(head)
    else:
        end = min(len(head), len(new) - len(after))
    end = max(0, min(end, KEEP_MAX))
    marker = new.rfind(REDACTED, max(0, end - len(REDACTED) + 1), end + len(REDACTED) - 1)
    if marker != -1:
        end = marker + len(REDACTED)
    return new[:end]


def _kept_tail(before: str, tail: str) -> str:
    """The part of `redact(before + tail)` that stands for `tail`; see `_kept_head`."""
    new = _redact_all(before + tail)[0]
    if new.startswith(before):
        start = len(before)
    elif new.endswith(tail):
        start = len(new) - len(tail)
    else:
        start = max(len(before), len(new) - len(tail))
    start = min(len(new), max(start, len(new) - KEEP_MAX))
    marker = new.find(REDACTED, max(0, start - len(REDACTED) + 1), start + len(REDACTED) - 1)
    if marker != -1:
        start = marker
    return new[start:]


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
                lines[index], count = _cut_line(line)
                found += count
        text = "\n".join(lines)
    text, total = _redact_all(text)
    return text, total + found


def redact_text(text: str) -> str:
    """`redact_with_count` without the count."""
    return redact_with_count(text)[0]
