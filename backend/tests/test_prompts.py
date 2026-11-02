import pytest

from bleep.prompts import PromptError, all_prompt_files, load, placeholders


def test_load_fills_placeholders(tmp_prompts):
    assert load("demo/hello", name="Bleep") == "Hi Bleep {x}"


def test_load_rejects_missing_and_unused(tmp_prompts):
    with pytest.raises(PromptError):
        load("demo/hello")
    with pytest.raises(PromptError):
        load("demo/hello", name="a", extra="b")


def test_load_rejects_unknown_prompt(tmp_prompts):
    with pytest.raises(PromptError):
        load("demo/nope")


@pytest.mark.parametrize(
    "text", ["Hi {name.attr}", "Hi {0}", "Hi {name!r}", "Hi {name:>5}", "Hi {"]
)
def test_placeholders_reject_anything_but_plain_names(text):
    with pytest.raises(PromptError):
        placeholders(text)


def test_placeholders_lists_names_and_skips_escaped_braces():
    assert placeholders("{a} {{b}} {a} {c}") == {"a", "c"}


def test_every_prompt_file_parses():
    for path in all_prompt_files():
        assert placeholders(path.read_text(encoding="utf-8")) is not None, path
