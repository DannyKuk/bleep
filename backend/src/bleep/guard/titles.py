"""An incident's title: Bleep's words for its first signal and its subject, a service's key as
host text when it is not plain (#311), and the repeat note a folded incident's title ends with."""

import re
import unicodedata

from bleep.guard.pipeline import is_plain_name
from bleep.guard.quote_lookalikes import QUOTE_LOOKALIKES
from bleep.guard.redact import redact_text
from bleep.guard.sanitize import strip_control
from bleep.schemas.incidents import Signal, Subject

_SEEN = re.compile(r"\(seen \d+ times since [^)]*\)$")


def seen_note(title: str) -> str:
    """The "(seen N times since …)" a repeat's title ends with; empty for any other title."""
    found = _SEEN.search(title)
    return found.group(0) if found else ""


# The kinds `hosts.discovery` puts before a service key: Bleep's own words, not the label's.
_KEY_KINDS = ("compose:", "container:", "id:")
TITLE_NAME_CHARS = 40  # a name that is not plain, cut to this in a title, "…" included
_MARKS = ("Mn", "Me", "Cs")  # combining marks (a `"` can be drawn on a letter), lone surrogates
_BLANKS = re.compile(r"\s+")
# A name in a title is shown inside straight double quotes, so nothing in it may read as one:
# each character of `QUOTE_LOOKALIKES` becomes `'`, and a run of `'` one, since two read as `"`.
# `ŉ` is drawn as `'n`, so it is written so before the run is collapsed.
_PLAIN_QUOTES = {ord(c): "'" for c in QUOTE_LOOKALIKES} | {0x0149: "'n"}
_APOSTROPHES = re.compile(r"''+")
_SKIN_TONES = ("\U0001f3fb", "\U0001f3ff")
_REGIONAL = ("\U0001f1e6", "\U0001f1ff")


def split_service_id(subject: Subject) -> tuple[str, str]:
    """The service id split into Bleep's own prefix (`<host>/<kind>:`) and the key a label
    wrote. An id that does not start with its host gives no prefix: all of it is the label's."""
    service_id = subject.service_id or ""
    host = f"{subject.host_id}/"
    if not service_id.startswith(host):
        return "", service_id
    key = service_id[len(host) :]
    kind = next((k for k in _KEY_KINDS if key.startswith(k)), "")
    return host + kind, key[len(kind) :]


def _regional(char: str) -> bool:
    return _REGIONAL[0] <= char <= _REGIONAL[1]


def _joins_previous(text: str, at: int) -> bool:
    """Whether `text[at]` is drawn as one with the character before it: a spacing mark, a skin
    tone, or the second letter of a flag. (Zero-width joiners and variation selectors are gone
    by then, `strip_control` drops them.)"""
    char = text[at]
    if unicodedata.category(char) == "Mc" or _SKIN_TONES[0] <= char <= _SKIN_TONES[1]:
        return True
    if not _regional(char):
        return False
    flags = 0  # the flag letters before it: an odd number leaves it the second of a pair
    while flags < at and _regional(text[at - 1 - flags]):
        flags += 1
    return flags % 2 == 1


def _cut(text: str, limit: int) -> str:
    """`text` cut to `limit` characters with "…", never inside what draws as one character."""
    if len(text) <= limit:
        return text
    end = limit - 1
    while end > 0 and _joins_previous(text, end):
        end -= 1
    return text[:end].rstrip() + "…"


def title_name(name: str) -> str:
    """A name from a label as an incident title shows it: a plain one (`is_plain_name`) as it
    is, any other as host text: in double quotes, on one line, redacted, with nothing of its own
    that reads as a double quote, cut to 40 characters with "…"."""
    if is_plain_name(name):
        return name
    text = unicodedata.normalize("NFC", strip_control(name))
    text = "".join(c for c in text if unicodedata.category(c) not in _MARKS)
    text = _BLANKS.sub(" ", redact_text(text)).strip()
    text = _APOSTROPHES.sub("'", text.translate(_PLAIN_QUOTES))
    # Again: a secret in curly quotes (`“password”: “…”`) is found only once they read `'`.
    return f'"{_cut(redact_text(text), TITLE_NAME_CHARS)}"'


def build_title(subject: Subject, signals: list[Signal]) -> str:
    """The title an incident opens with ("container died on a/compose:shop"): Bleep's words for
    its first signal and its subject, a service's key through `title_name`. The host id is the
    hosts file's or the dashboard's, written by the operator, and stands as it is."""
    what = signals[0].kind.replace("_", " ") if signals else "incident"
    if not subject.service_id:
        return f"{what} on {subject.host_id}"
    prefix, key = split_service_id(subject)
    return f"{what} on {prefix}{title_name(key)}"
