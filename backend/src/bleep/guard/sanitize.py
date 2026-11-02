"""Make raw log text safe and small enough to hand to a model."""

import itertools
import re

MAX_CHARS = 8000
MAX_RUN = 3

_ANSI = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"  # CSI: colours, cursor moves, erase
    r"|\x1b\][^\x07\x1b\n]*(?:\x07|\x1b\\)?"  # OSC: window title, hyperlinks
    r"|\x1b[@-Z\\-_]"  # two-character escapes
)
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")  # all but \n and \t
# Bidi overrides, zero-width characters and the byte-order mark hide text from a human reader.
_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]")


def strip_control(text: str | bytes) -> str:
    """Decode (bytes use errors="replace"), normalise newlines, drop ANSI and control characters."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _ANSI.sub("", text)
    return _INVISIBLE.sub("", _CONTROL.sub("", text))


def collapse_repeats(text: str) -> str:
    """Replace a run of more than 3 identical lines with its first line and a repeat count."""
    out: list[str] = []
    for line, group in itertools.groupby(text.split("\n")):
        count = len(list(group))
        if count > MAX_RUN:
            out += [line, f"[… repeated {count - 1} times]"]
        else:
            out += [line] * count
    return "\n".join(out)


def sanitize(text: str | bytes, max_chars: int = MAX_CHARS) -> str:
    """Strip, collapse repeated lines, then keep the last `max_chars` (the end of a log matters).

    When something is cut, the result starts with `[… N chars cut]`.
    """
    text = collapse_repeats(strip_control(text))
    if len(text) <= max_chars:
        return text
    return f"[… {len(text) - max_chars} chars cut]\n{text[-max_chars:]}"
