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
# byte-order mark, U+180E, the tag block; plus variation selectors, and the characters that are
# not Cf but draw nothing: the combining grapheme joiner U+034F, the Khmer inherent vowels
# U+17B4 and U+17B5, the Braille blank U+2800, and the Hangul and halfwidth Hangul fillers
# U+115F, U+1160, U+3164 and U+FFA0. They hide text from a human reader and can split a secret
# or a tag so a pattern misses it. The dashboard marks the same set
# (frontend/lib/invisible-ranges.json, pinned to this by a test).
INVISIBLE_CHARS = (
    "[­؀-؅؜۝܏࢐࢑࣢᠋-᠏​-‏"
    "‪-‮⁠-⁤⁦-⁯︀-️﻿￹-￻"
    "\U000110bd\U000110cd\U00013430-\U0001343f\U0001bca0-\U0001bca3\U0001d173-\U0001d17a"
    "\U000e0000-\U000e007f\U000e0100-\U000e01ef"
    "\u034f\u115f\u1160\u17b4\u17b5\u2800\u3164\uffa0]"
)
_INVISIBLE = re.compile(INVISIBLE_CHARS)


def normalise_newlines(text: str | bytes) -> str:
    """Decode (bytes use errors="replace"); CRLF, CR, U+2028 and U+2029 become a newline."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    return (
        text.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\u2028", "\n")
        .replace("\u2029", "\n")
    )


def tail_lines(text: str, limit: int) -> tuple[str, int]:
    """Keep the last `limit` characters, starting at a line boundary; return it and the number
    of characters dropped. This is the first bound on the work every later step does."""
    if len(text) <= limit:
        return text, 0
    kept = text[-limit:]
    newline = kept.find("\n")
    if newline != -1:
        kept = kept[newline + 1 :]
    return kept, len(text) - len(kept)


def strip_control(text: str | bytes) -> str:
    """Normalise newlines, drop ANSI sequences, control and invisible characters."""
    text = _ANSI.sub("", normalise_newlines(text))
    return _INVISIBLE.sub("", _CONTROL.sub("", text))


_SPACE = re.compile(r"\s")
VALUE_END = re.compile(r"[\s\"',;&]")  # where an unquoted or quoted value can end
MAX_LEAD = 200


def cap_lines(text: str, max_line: int = MAX_LINE) -> str:
    """Cut each line longer than `max_line` in the middle, keeping its head and tail.

    This bounds the work every later pattern can do on one line. The cut note gets a line of its
    own (see `join_cut`).
    """
    if len(text) <= max_line:
        return text
    half = (max_line - 300) // 2
    out = []
    for line in text.split("\n"):
        if len(line) > max_line:
            head, tail = cut_points(line, half)
            lead = line[: len(line) - len(line.lstrip(" \t"))]
            line = join_cut(line[:head], tail - head, line[tail:], lead)
        out.append(line)
    return "\n".join(out)


def join_cut(head: str, dropped: int, tail: str, lead: str = "") -> str:
    """The kept head, the `[… N chars cut]` note and the kept tail, each on its own line.

    On its own line the note can never be swallowed by a value before it. The note and the
    tail start with `lead` (the line's indentation or log prefix), so a cut line inside a YAML
    block keeps its indentation.
    """
    lead = lead[:MAX_LEAD]
    return f"{head}\n{lead}[… {dropped} chars cut]\n{lead}{tail}"


def cut_points(line: str, half: int, at_value_ends: bool = False) -> tuple[int, int]:
    """Where the kept head ends and the kept tail starts: the nearest whitespace within 256
    characters, so a secret of 200 characters or less is never split by the cut.

    With `at_value_ends`, a quote, `,`, `;` or `&` also ends a value, so compact JSON and cookie
    lines are cut between values. The head still moves back at most 256 characters; the tail moves
    forward to the first value end however far it is, so the rest of a value that straddles the
    tail cut is dropped. With no value end after it, the tail stays (a line without one keeps its
    head and tail).
    """
    ends = VALUE_END if at_value_ends else _SPACE
    head = half
    for i in range(half, max(half - SNAP, 0), -1):
        if ends.match(line, i):
            head = i
            break
    tail = len(line) - half
    found = ends.search(line, tail, len(line) if at_value_ends else tail + SNAP)
    if found:
        tail = found.start()
    return head, max(head, tail)


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
