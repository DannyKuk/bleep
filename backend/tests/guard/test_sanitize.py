from bleep.guard.sanitize import sanitize, strip_control


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
