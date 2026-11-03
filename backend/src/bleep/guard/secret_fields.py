"""Refuse secrets in what a person sends to be stored as settings.

Settings live in SQLite, and parts of them (rules, URLs) reach the model, so secrets must never
get in. A body is refused when any key at any depth looks like a secret's name (`token`, `apiKey`,
`DB_PASSWORD`, `x-api-key`), or any string in it, key or value, is changed by redaction (a token in
a rule, a password in a URL). Only `allowed` names, the settings models' own fields, are trusted:
`incident_tokens` is a budget, not a token. See docs/design/settings.md.
"""

import re
from collections.abc import Collection
from typing import Any

from bleep.guard.redact import WORDS, redact_text
from bleep.guard.untrusted import fold

# Matched against the name in lower case with everything but letters and digits removed, so case,
# `_`, `-` and `.` change nothing: API_KEY, apiKey and api-key are all "apikey".
_SECRET_PARTS = (
    "token", "secret", "passw", "passphrase", "apikey", "privatekey", "accesskey", "credential",
    "authorization", "bearer", "cookie",
)  # fmt: skip
_SECRET_WORDS = {"pwd", "pw", "pass", "key", "keys"}
_NON_ALNUM = re.compile(r"[^a-z0-9]")
_QUERY_NAME = re.compile(r"[?&;]([^=&;#\s]{1,64})=")  # ?key=..., &api_key=... in a URL


def is_secret_field(name: str) -> bool:
    """Whether `name` looks like the name of a secret: stricter than redaction's own check,
    because a settings key that only might be a secret is still refused. Full-width, look-alike
    and invisible characters are folded first, so `ｔｏｋｅｎ` and `pаssword` (Cyrillic а) count."""
    return _secret_name(name) or _secret_name(fold(name))


def find_secrets(data: Any, allowed: Collection[str] = ()) -> list[str]:
    """Where `data` (decoded JSON) carries something secret-shaped, as paths like
    `hosts.a.rules[0]`; empty when it carries nothing. A path names keys, never a value.
    Walked with a stack, so a deeply nested body is refused, not a crash."""
    found: list[str] = []
    allowed = set(allowed)
    stack: list[tuple[str, Any]] = [("", data)]
    while stack:
        path, item = stack.pop()
        if isinstance(item, dict):
            children = []
            for key, value in item.items():
                name = str(key)
                here = f"{path}.{name}" if path else name
                if (name not in allowed and is_secret_field(name)) or _holds_secret(name):
                    found.append(here)
                else:
                    children.append((here, value))
            stack.extend(reversed(children))
        elif isinstance(item, list):
            stack.extend(reversed([(f"{path}[{i}]", v) for i, v in enumerate(item)]))
        elif isinstance(item, str) and _holds_secret(item):
            found.append(path or "body")
    return found


def _secret_name(name: str) -> bool:
    flat = _NON_ALNUM.sub("", name.lower())
    if any(part in flat for part in _SECRET_PARTS):
        return True
    words = [w.lower() for w in WORDS.findall(name)]
    return bool(words) and (words[-1] in ("key", "keys") or any(w in _SECRET_WORDS for w in words))


def _holds_secret(text: str) -> bool:
    """Redaction would change it, as written or folded, or it is a URL with a secret-named
    query parameter (`?key=...`)."""
    folded = fold(text)
    return (
        redact_text(text) != text
        or redact_text(folded) != folded
        or any(_secret_name(m) for m in _QUERY_NAME.findall(folded))
    )
