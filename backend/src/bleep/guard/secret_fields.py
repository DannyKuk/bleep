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

# Matched against the name in lower case with everything but letters and digits removed, so case,
# `_`, `-` and `.` change nothing: API_KEY, apiKey and api-key are all "apikey".
_SECRET_PARTS = (
    "token", "secret", "passw", "passphrase", "apikey", "privatekey", "accesskey", "credential",
    "authorization", "bearer", "cookie",
)  # fmt: skip
_SECRET_WORDS = {"pwd", "pw", "pass", "key", "keys"}
_NON_ALNUM = re.compile(r"[^a-z0-9]")


def is_secret_field(name: str) -> bool:
    """Whether `name` looks like the name of a secret: stricter than redaction's own check,
    because a settings key that only might be a secret is still refused."""
    flat = _NON_ALNUM.sub("", name.lower())
    if any(part in flat for part in _SECRET_PARTS):
        return True
    words = [w.lower() for w in WORDS.findall(name)]
    return bool(words) and (words[-1] in ("key", "keys") or any(w in _SECRET_WORDS for w in words))


def find_secrets(data: Any, allowed: Collection[str] = ()) -> list[str]:
    """Where `data` (decoded JSON) carries something secret-shaped, as paths like
    `hosts.a.rules[0]`; empty when it carries nothing. A path names keys, never a value."""
    found: list[str] = []
    _walk(data, "", set(allowed), found)
    return found


def _walk(data: Any, path: str, allowed: set[str], found: list[str]) -> None:
    if isinstance(data, dict):
        for key, value in data.items():
            here = f"{path}.{key}" if path else str(key)
            name = str(key)
            if (name not in allowed and is_secret_field(name)) or _holds_secret(name):
                found.append(here)
                continue
            _walk(value, here, allowed, found)
    elif isinstance(data, list):
        for index, item in enumerate(data):
            _walk(item, f"{path}[{index}]", allowed, found)
    elif isinstance(data, str) and _holds_secret(data):
        found.append(path or "body")


def _holds_secret(text: str) -> bool:
    return redact_text(text) != text
