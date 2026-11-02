"""Make raw log text safe and small enough to hand to a model."""

import itertools
import re

MAX_CHARS = 8000
MAX_RUN = 3
MAX_LINE = 4000
SNAP = 256

_ANSI = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"  # CSI: colours, cursor moves, erase
    r"|\x1b\][^\x07\x1b\n]*(?:\x07|\x1b\\)?"  # OSC: window title, hyperlinks
    r"|\x1b[@-Z\\-_]"  # two-character escapes
)
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")  # all but \n and \t
# Every Unicode "format" (Cf) character: soft hyphen, zero-width, bidi marks and overrides, the
# byte-order mark, U+180E, the tag block; plus variation selectors. They hide text from a human
# reader and can split a secret or a tag so a pattern misses it.
_INVISIBLE = re.compile(
    "[­؀-؅؜۝܏࢐࢑࣢᠋-᠏​-‏"
    "‪-‮⁠-⁤⁦-⁯︀-️﻿￹-￻"
    "\U000110bd\U000110cd\U00013430-\U0001343f\U0001bca0-\U0001bca3\U0001d173-\U0001d17a"
    "\U000e0000-\U000e007f\U000e0100-\U000e01ef]"
)


def strip_control(text: str | bytes) -> str:
    """Decode (bytes use errors="replace"), normalise newlines, drop ANSI and control characters."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", "\n")
    text = text.replace(" ", "\n")
    text = _ANSI.sub("", text)
    return _INVISIBLE.sub("", _CONTROL.sub("", text))


def cap_lines(text: str, max_line: int = MAX_LINE) -> str:
    """Cut each line longer than `max_line` in the middle, keeping its head and tail.

    This bounds the work every later pattern can do on one line, whatever the log contains.
    """
    if len(text) <= max_line:
        return text
    half = (max_line - 40) // 2
    out = []
    for line in text.split("\n"):
        if len(line) > max_line:
            head, tail = _cut_points(line, half)
            line = f"{line[:head]}[… {tail - head} chars cut]{line[tail:]}"
        out.append(line)
    return "\n".join(out)


def _cut_points(line: str, half: int) -> tuple[int, int]:
    """Where the kept head ends and the kept tail starts: the nearest whitespace within 256
    characters, so a secret of 200 characters or less is never split by the cut."""
    head = half
    for i in range(half, max(half - SNAP, 0), -1):
        if line[i].isspace():
            head = i
            break
    tail = len(line) - half
    for i in range(tail, min(tail + SNAP, len(line))):
        if line[i].isspace():
            tail = i
            break
    return head, tail


def collapse_repeats(text: str) -> str:
    """Replace a run of more than 3 identical lines with its first line and a repeat count."""
    out: list[str] = []
    for line, group in itertools.groupby(text.split("\n")):
        count = sum(1 for _ in group)
        if count > MAX_RUN:
            out += [line, f"[… repeated {count - 1} times]"]
        else:
            out += [line] * count
    return "\n".join(out)


def cut_tail(text: str, max_chars: int = MAX_CHARS, already_cut: int = 0) -> str:
    """Keep the last `max_chars`, starting the result with `[… N chars cut]` when cutting.

    `already_cut` counts characters a caller dropped earlier, so N reports the original length.
    """
    total = already_cut + max(0, len(text) - max_chars)
    if total == 0:
        return text
    return f"[… {total} chars cut]\n{text[-max_chars:]}"


def sanitize(text: str | bytes, max_chars: int = MAX_CHARS) -> str:
    """Strip, cap long lines, collapse repeated lines, then keep the last `max_chars`.

    The end of a log matters most, so the tail is kept.
    """
    return cut_tail(collapse_repeats(cap_lines(strip_control(text))), max_chars)
