import pytest

from bleep import prompts
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


def test_an_override_stands_in_for_the_file_until_it_is_cleared(tmp_prompts):
    prompts.use_overrides({"demo/hello": "Hello {name}, from the dev panel"})
    assert load("demo/hello", name="Bleep") == "Hello Bleep, from the dev panel"
    prompts.use_overrides({})
    assert load("demo/hello", name="Bleep") == "Hi Bleep {x}"


def test_an_override_is_filled_with_the_same_checks_as_a_file(tmp_prompts):
    prompts.use_overrides({"demo/hello": "Hello {name}"})
    with pytest.raises(PromptError):
        load("demo/hello")


@pytest.mark.parametrize(
    "text",
    [
        "Hello",  # the file's {name} is missing
        "Hello {name} {other}",  # a placeholder the callers never fill
        "Hello {name} {",  # a stray brace
        "Hello {name} }",
        "Hello {name!r}",
        "Hello {name}\x00",  # a control byte
        "Hello {name}\x85",  # a C1 control (NEL)
        "Hello {name}\x9b31m",  # a C1 CSI
        "Hello {name} ‮deliver‬",  # a bidi override
        "Hello {name} ⁦isolated⁩",  # a bidi isolate
        "Hello {name} \u200eleft",  # a left-to-right mark
        "Hello {name} \u200fright",  # a right-to-left mark
        "Hello {name} \u061carabic",  # an Arabic letter mark
        "Hello\u200b {name}",  # a zero-width space
        "\ufeffHello {name}",  # a byte-order mark
    ],
)
def test_check_override_refuses_what_the_loader_would(tmp_prompts, text):
    with pytest.raises(PromptError):
        prompts.check_override("demo/hello", text)


def test_no_prompt_file_holds_a_character_an_override_may_not():
    for path in all_prompt_files():
        assert prompts._CONTROL.search(path.read_text(encoding="utf-8")) is None, path


def test_check_override_accepts_the_files_placeholders_and_literal_braces(tmp_prompts):
    prompts.check_override("demo/hello", "Hey {name}, {{json}} is literal\n\tok")


def test_check_override_refuses_an_unknown_prompt_and_the_eval_prompts(tmp_prompts):
    (tmp_prompts / "evals").mkdir()
    (tmp_prompts / "evals" / "judge.md").write_text("Judge", encoding="utf-8")
    with pytest.raises(PromptError):
        prompts.check_override("demo/nope", "Hi")
    with pytest.raises(PromptError):
        prompts.check_override("evals/judge", "Judge")
    assert prompts.overridable_names() == ["demo/hello"]


@pytest.mark.parametrize(
    "name", ["graph/system", "notify/proposal", "notify/reminder", "notify/answer_approved"]
)
def test_the_untrusted_data_rule_and_the_texts_an_approver_reads_cannot_be_overridden(name):
    assert name not in prompts.overridable_names()
    with pytest.raises(PromptError):
        prompts.check_override(name, prompts.file_text(name))


def test_no_telegram_text_is_overridable_and_the_graph_prompts_still_are():
    names = prompts.overridable_names()
    assert not [name for name in names if name.startswith(("notify/", "evals/"))]
    assert {"graph/investigate", "graph/propose", "watcher/recovery_line"} <= set(names)


@pytest.mark.parametrize(
    "name", ["../secrets", "demo/../demo/hello", "/etc/passwd", "demo\\hello", "readme"]
)
def test_a_name_outside_the_prompt_files_is_unknown(tmp_prompts, name):
    (tmp_prompts / "README.md").write_text("How prompts work", encoding="utf-8")
    with pytest.raises(PromptError):
        prompts.file_text(name)
