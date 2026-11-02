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
