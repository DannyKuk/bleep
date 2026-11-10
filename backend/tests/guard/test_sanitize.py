import json
import re
import unicodedata
from pathlib import Path

import pytest

from bleep.guard.sanitize import INVISIBLE_CHARS, sanitize, strip_control


def test_strips_ansi_and_controls_keeps_newline_tab():
    raw = "\x1b[31mred\x1b[0m\x1b]0;title\x07 a\x00b\tc\nd\x1b[2K\x7f"
    out = sanitize(raw)
    assert out == "red ab\tc\nd"
    assert "\x1b" not in out


def test_normalises_crlf_and_cr():
    assert sanitize("a\r\nb\rc") == "a\nb\nc"


def test_decodes_bytes_with_replace():
    assert sanitize(b"ok \xff\xfe end") == "ok �� end"


def test_collapses_long_runs_only():
    assert sanitize("a\na\na\nb") == "a\na\na\nb"
    out = sanitize("\n".join(["same"] * 10 + ["end"]))
    assert out == "same\n[… repeated 9 times]\nend"


def test_keeps_the_tail_with_cut_note():
    text = "\n".join(f"line {i}" for i in range(2000))
    out = sanitize(text, max_chars=100)
    assert out.startswith("[… ")
    assert out.endswith("line 1999")
    assert "chars cut]" in out.splitlines()[0]
    assert len(out) <= 100 + 40


def test_short_text_untouched():
    assert sanitize("hello") == "hello"


def test_bidi_and_zero_width_removed():
    assert strip_control("a‮b​c") == "abc"


def test_every_format_character_is_stripped():
    import sys
    import unicodedata

    leftovers = [
        hex(cp)
        for cp in range(sys.maxunicode + 1)
        if unicodedata.category(chr(cp)) == "Cf" and strip_control(f"a{chr(cp)}b") != "ab"
    ]
    assert leftovers == []
    for cp in [*range(0xE0000, 0xE0080), *range(0xFE00, 0xFE10), *range(0xE0100, 0xE01F0)]:
        assert strip_control(f"a{chr(cp)}b") == "ab"


def test_cap_lines_keeps_head_and_tail():
    from bleep.guard.sanitize import cap_lines

    line = "H" * 3000 + "M" * 100_000 + "T" * 3000
    out = cap_lines(f"short\n{line}\nafter")
    first, head, note, tail, last = out.split("\n")
    assert first == "short" and last == "after"
    assert head.startswith("HHH") and tail.endswith("TTT") and len(head) + len(tail) <= 4000
    assert note == f"[… {len(line) - len(head) - len(tail)} chars cut]"
    assert cap_lines(out) == out


def test_cut_note_keeps_the_line_indentation():
    from bleep.guard.sanitize import cap_lines

    out = cap_lines("    " + "x " * 5000)
    head, note, tail = out.split("\n")
    assert note.startswith("    [… ") and tail.startswith("    ")


def test_characters_that_draw_nothing_but_are_not_format_characters_are_stripped():
    assert strip_control("a\u034f\u17b4\u17b5\u115f\u1160\u3164\uffa0b") == "ab"


def test_the_dashboard_shows_exactly_the_characters_the_guard_treats_as_invisible():
    ranges = Path(__file__).resolve().parents[3] / "frontend" / "lib" / "invisible-ranges.json"
    listed = {c for a, b in json.loads(ranges.read_text()) for c in range(a, b + 1)}
    guarded = {c for c in range(0x110000) if re.fullmatch(INVISIBLE_CHARS, chr(c))}

    assert listed == guarded


def test_the_braille_blank_is_stripped():
    assert strip_control("a\u2800b") == "ab"


def test_the_invisible_set_covers_every_format_character_of_the_running_python():
    missing = [
        c
        for c in range(0x110000)
        if unicodedata.category(chr(c)) == "Cf" and not re.fullmatch(INVISIBLE_CHARS, chr(c))
    ]

    assert not missing, (
        f"Unicode {unicodedata.unidata_version} has format (Cf) characters "
        f"INVISIBLE_CHARS lacks: "
        f"{', '.join(f'U+{c:04X}' for c in missing)}. Add them to INVISIBLE_CHARS in "
        "guard/sanitize.py, then update frontend/lib/invisible-ranges.json to match."
    )


HIGH, LOW = chr(0xD83D), chr(0xDE00)  # the UTF-16 pair of U+1F600
TAG_HIGH, TAG_LOW = chr(0xDB40), chr(0xDC01)  # the UTF-16 pair of U+E0001, a tag character


@pytest.mark.parametrize(
    ("raw", "kept"),
    [
        ("a\ud800b", "ab"),
        ("a\udfffb", "ab"),
        (f"end {HIGH}", "end "),
        ("\udc00start", "start"),
        ("\udfff\ud800", ""),
        (f"a{HIGH}{LOW}b", "a\U0001f600b"),
        (f"\ud800{HIGH}{LOW}", "\U0001f600"),
        ("a\U0001f600b", "a\U0001f600b"),
        (f"{TAG_HIGH}{TAG_LOW}x", "x"),
    ],
)
def test_lone_surrogates_are_dropped_and_pairs_kept_so_the_output_encodes(raw, kept):
    out = strip_control(raw)

    assert out == kept
    out.encode("utf-8")


def test_lone_surrogates_from_json_never_reach_the_sanitized_text():
    escaped_pair = "".join(f"\\u{ord(c):04x}" for c in (HIGH, LOW))
    field = json.loads(f'"\\ud800 a \\udfff b {escaped_pair} c \\ud83d"')

    out = sanitize(field)

    assert out == " a  b \U0001f600 c "
    out.encode("utf-8")
