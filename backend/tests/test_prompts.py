from pathlib import Path

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
    "name",
    [
        "graph/system",
        "graph/propose_mcp",
        "graph/verify_unproven",
        "notify/proposal",
        "notify/reminder",
        "notify/answer_approved",
    ],
)
def test_the_untrusted_data_rule_and_the_texts_an_approver_reads_cannot_be_overridden(name):
    assert name not in prompts.overridable_names()
    with pytest.raises(PromptError):
        prompts.check_override(name, prompts.file_text(name))


OVERRIDABLE = [
    "graph/diagnose",
    "graph/diagnose_missing",
    "graph/investigate",
    "graph/investigate_again",
    "graph/propose",
    "graph/propose_retry",
    "graph/propose_unsure",
    "graph/report",
    "graph/report_facts",
    "graph/verify_failed",
    "llm/json_fallback",
    "llm/json_retry",
    "watcher/recovery_line",
]
UNTRUSTED_RULES = {
    "graph/investigate": "untrusted/investigate",
    "graph/diagnose": "untrusted/diagnose",
    "graph/propose": "untrusted/propose",
    "watcher/recovery_line": "untrusted/recovery_line",
}
FIXED_PARTS = {"graph/diagnose": {"planted_in_rule": "untrusted/diagnose_planted_in"}}


def prompt_names() -> list[str]:
    return [
        p.relative_to(prompts.PROMPTS_DIR).with_suffix("").as_posix() for p in all_prompt_files()
    ]


def test_the_overridable_prompts_are_exactly_these():
    """A change to this list is a decision: say why in docs/design/dev-panel.md."""
    assert prompts.overridable_names() == OVERRIDABLE


def test_every_prompt_file_is_overridable_or_not_by_an_explicit_choice():
    """A new prompt file fails here until it is named in `OVERRIDABLE` or left out on purpose."""
    for name in prompt_names():
        fixed = name.startswith(prompts.NOT_OVERRIDABLE_AREAS) or name in prompts.NOT_OVERRIDABLE
        assert (name in prompts.OVERRIDABLE) != fixed, name
    assert set(prompt_names()) >= prompts.OVERRIDABLE


def test_a_new_prompt_file_is_not_overridable_by_default(tmp_prompts):
    (tmp_prompts / "demo" / "new_rule.md").write_text("Never do that.", encoding="utf-8")
    assert "demo/new_rule" not in prompts.overridable_names()
    with pytest.raises(PromptError):
        prompts.check_override("demo/new_rule", "Do that.")


@pytest.mark.parametrize(("name", "rule"), UNTRUSTED_RULES.items())
def test_the_untrusted_data_rules_are_their_own_files_and_cannot_be_overridden(name, rule):
    assert rule not in prompts.overridable_names()
    with pytest.raises(PromptError):
        prompts.check_override(rule, prompts.file_text(rule))
    text = prompts.file_text(name)
    assert "untrusted_rule" in placeholders(text)
    without = text.replace("{untrusted_rule}", "")
    with pytest.raises(PromptError, match="missing: {untrusted_rule}"):
        prompts.check_override(name, without)


def test_the_planted_in_instruction_is_a_fixed_part_an_override_must_keep_a_place_for():
    rule = "untrusted/diagnose_planted_in"
    assert rule not in prompts.overridable_names()
    with pytest.raises(PromptError):
        prompts.check_override(rule, prompts.file_text(rule))
    text = prompts.file_text("graph/diagnose")
    assert "planted_in" not in text.replace("{planted_in_rule}", "")
    without = text.replace("{planted_in_rule}", "")
    with pytest.raises(PromptError, match="missing: {planted_in_rule}"):
        prompts.check_override("graph/diagnose", without)


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


FIXTURES = Path(__file__).parent / "fixtures" / "prompts"


@pytest.mark.parametrize(("name", "rule"), UNTRUSTED_RULES.items())
def test_the_four_prompts_render_as_they_did_before_their_rule_moved_out(name, rule):
    """The fixtures were rendered from the files at c0afd0f, before #147 moved each rule into
    `untrusted/`, with every placeholder filled as `<name>`; `{mcp}` came later (#118) and is
    filled as it is with the MCP plugin off."""
    extra = {field: load(rule).strip() for field, rule in FIXED_PARTS.get(name, {}).items()}
    fields = placeholders(prompts.file_text(name)) - {"untrusted_rule"} - set(extra)
    values = {field: f"<{field}>" for field in fields}
    if "mcp" in values:
        values["mcp"] = ""  # the MCP plugin off (#118): the fixture predates `{mcp}`
    rendered = load(
        name,
        **values,
        **extra,
        untrusted_rule=load(rule).strip(),  # as the code that builds the prompt fills it
    )
    expected = (FIXTURES / f"{name.replace('/', '-')}.txt").read_text(encoding="utf-8")
    assert rendered.encode() == expected.encode()
