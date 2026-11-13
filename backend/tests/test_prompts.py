import re
from pathlib import Path
from typing import get_args

import pytest

from bleep import prompts
from bleep.graph.language import language_rule
from bleep.prompts import PromptError, all_prompt_files, file_text, load, placeholders
from bleep.schemas.settings import Language
from bleep.schemas.shown import Shown


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
    "graph/diagnose_de",
    "graph/diagnose_missing",
    "graph/investigate",
    "graph/investigate_again",
    "graph/investigate_mcp",
    "graph/look_diagnose",
    "graph/look_investigate",
    "graph/look_report",
    "graph/propose",
    "graph/propose_de",
    "graph/propose_retry",
    "graph/propose_unsure",
    "graph/report",
    "graph/report_de",
    "graph/report_facts",
    "graph/url_checks",
    "graph/url_checks_diagnose",
    "graph/url_checks_none",
    "graph/url_checks_none_diagnose",
    "graph/verify_failed",
    "llm/json_fallback",
    "llm/json_retry",
    "watcher/recovery_line",
    "watcher/recovery_line_de",
]
UNTRUSTED_RULES = {
    "graph/investigate": "untrusted/investigate",
    "graph/diagnose": "untrusted/diagnose",
    "graph/propose": "untrusted/propose",
    "watcher/recovery_line": "untrusted/recovery_line",
}
# Fixed parts filled into a placeholder besides `{untrusted_rule}`.
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
    without = prompts.file_text("graph/diagnose").replace("{planted_in_rule}", "")
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
def test_the_prompts_with_a_fixed_part_render_as_they_did_before_it_moved_out(name, rule):
    """The fixtures were rendered from the files at c0afd0f, before #147 moved each rule (and
    #165 Diagnose's `planted_in` bullet) into `untrusted/`, with every placeholder filled as
    `<name>`; `{mcp}` came later (#118) and is filled as it is with the MCP plugin off.
    `graph-diagnose.txt` was rendered again when #108 defined the categories in the prompt,
    when #105 narrowed the `planted_in` bullet, when #357 asked for exact names and when #364
    asked for a setting's documented value and when #561 asked to name a setting's current
    value with the tool that showed it, `graph-investigate.txt` when #364 asked for that docs
    search, and `graph-propose.txt` when #276 kept Propose's plan to the steps the diagnosis
    needs, when #365 asked for `could_run` and when #561 kept it for a fix only the person can
    confirm. `{look}` (#410) and `{url_checks}` (#423) are filled as they are for a run no look
    opened, and `{language}` (#374) as it is in English, so the fixtures also show that such a
    run's prompt did not change."""
    extra = {field: load(rule).strip() for field, rule in FIXED_PARTS.get(name, {}).items()}
    fields = placeholders(prompts.file_text(name)) - {"untrusted_rule"} - set(extra)
    values = {field: f"<{field}>" for field in fields}
    if "mcp" in values:
        values["mcp"] = ""  # the MCP plugin off (#118): the fixture predates `{mcp}`
    if "look" in values:
        values["look"] = ""  # a run no look opened (#410): its prompt is the one before `{look}`
    if "url_checks" in values:
        values["url_checks"] = ""  # a run no look opened checks no URL in Gather (#423)
    if "language" in values:
        values["language"] = language_rule(name, "en")  # empty (#374)
    rendered = load(
        name,
        **values,
        **extra,
        untrusted_rule=load(rule).strip(),  # as the code that builds the prompt fills it
    )
    expected = (FIXTURES / f"{name.replace('/', '-')}.txt").read_text(encoding="utf-8")
    assert rendered.encode() == expected.encode()


# The prompts that write text the person reads, each with a `{language}` placeholder (#374).
WRITTEN_FOR_THE_PERSON = [
    "graph/diagnose",
    "graph/propose",
    "graph/report",
    "watcher/recovery_line",
]
# Each graph prompt's German rule: the fields its "Write ... in German" sentence names (only the
# ones the person reads), and those its "exactly as described above" sentence keeps. Diagnose's
# `missing` only steers Investigate's next round and `planted_in` is a closed list, so both stay.
GERMAN_FIELDS = {
    "graph/diagnose": {"root_cause", "detail"},
    "graph/propose": {"reason", "human_instructions"},
    "graph/report": {"summary", "cause", "follow_ups"},
}
KEPT_FIELDS = {
    "graph/diagnose": {"category", "source", "affected_services", "planted_in", "missing"},
    "graph/propose": {
        "kind",
        "action",
        "target",
        "host_id",
        "params",
        "could_run",
        "verification",
    },
}


def _sentence(rule: str, words: str) -> str:
    return next(s for s in re.split(r"(?<=\.) ", rule) if words in s)


def _fields(sentence: str) -> set[str]:
    return set(re.findall(r"`([a-z_]+)`", sentence))


def _rendered(name: str, language: Language) -> str:
    """`name` with every placeholder filled as `<name>`, as for a run no look opened with the
    MCP plugin off, its fixed parts and `{language}` filled as the code fills them."""
    fields = placeholders(file_text(name))
    values = {field: f"<{field}>" for field in fields}
    values |= {field: "" for field in ("look", "mcp") if field in fields}
    if name in UNTRUSTED_RULES:
        values["untrusted_rule"] = load(UNTRUSTED_RULES[name]).strip()
    for field, rule in FIXED_PARTS.get(name, {}).items():
        values[field] = load(rule).strip()
    values["language"] = language_rule(name, language)
    return load(name, **values)


def test_the_prompts_written_for_the_person_have_a_language_placeholder_and_a_german_rule():
    with_placeholder = [n for n in prompt_names() if "language" in placeholders(file_text(n))]
    assert with_placeholder == WRITTEN_FOR_THE_PERSON
    german = [n.removesuffix("_de") for n in prompt_names() if n.endswith("_de")]
    assert german == WRITTEN_FOR_THE_PERSON
    for name in WRITTEN_FOR_THE_PERSON:
        assert placeholders(file_text(f"{name}_de")) == set(), name


def test_in_english_the_report_prompt_renders_as_it_did_before_the_language_placeholder():
    """`graph-report.txt` was rendered from `graph/report.md` before #374 added `{language}`,
    with every placeholder filled as `<name>` and `{look}` empty."""
    expected = (FIXTURES / "graph-report.txt").read_text(encoding="utf-8")
    assert _rendered("graph/report", "en").encode() == expected.encode()


@pytest.mark.parametrize("name", WRITTEN_FOR_THE_PERSON)
def test_in_german_a_prompt_is_the_english_one_with_its_german_rule_at_the_placeholder(name):
    english, german = _rendered(name, "en"), _rendered(name, "de")
    rule = load(f"{name}_de").strip()
    assert language_rule(name, "de") == "\n\n" + rule
    assert german.count(rule) == 1
    assert german.replace("\n\n" + rule, "", 1) == english
    # The placeholder ends a line, so the rule is a paragraph of its own.
    assert file_text(name).split("{language}")[1].startswith("\n")
    assert "German" in rule


@pytest.mark.parametrize("name", list(GERMAN_FIELDS))
def test_a_german_rule_names_exactly_the_fields_the_person_reads(name):
    rule = load(f"{name}_de").strip()
    written = _fields(_sentence(rule, "in German"))
    assert written == GERMAN_FIELDS[name]
    if name in KEPT_FIELDS:
        assert _fields(_sentence(rule, "exactly as described above")) == KEPT_FIELDS[name]


def test_the_german_report_asks_for_the_cause_in_english_too():
    rule = load("graph/report_de")
    assert "`cause_en`: the same root cause as `cause`, written in English" in rule


def test_the_german_recovery_rule_asks_for_the_sentence_in_german():
    assert "write the sentence in German" in load("watcher/recovery_line_de")


def test_in_english_no_prompt_written_for_the_person_names_a_language():
    for name in WRITTEN_FOR_THE_PERSON:
        assert "German" not in _rendered(name, "en"), name


def test_english_is_the_file_itself_and_another_language_its_twin_in_the_areas_folder():
    assert prompts.in_language("notify/proposal", "en") == "notify/proposal"
    assert prompts.in_language("notify/proposal", "de") == "notify/de/proposal"
    assert prompts.in_language("notify/seen/oom", "de") == "notify/de/seen/oom"
    assert prompts.in_language("talk/busy", "de") == "talk/de/busy"


@pytest.mark.parametrize("name", ["graph/propose", "talk/read", "talk/message", "talk/log_head"])
def test_a_text_the_model_reads_or_the_talk_log_export_has_no_twin(name):
    assert prompts.in_language(name, "en") == name
    with pytest.raises(PromptError):
        prompts.in_language(name, "de")


def test_an_unknown_language_is_refused():
    with pytest.raises(PromptError):
        prompts.in_language("notify/proposal", "fr")


def test_every_text_a_person_reads_has_a_twin_in_every_language_with_the_same_placeholders():
    assert prompts.twin_problems() == []


def _translated_area(root: Path) -> None:
    (root / "notify" / "de").mkdir(parents=True)
    (root / "notify" / "hi.md").write_text("Hi {name}", encoding="utf-8")
    (root / "notify" / "de" / "hi.md").write_text("Hallo {name}", encoding="utf-8")


def test_the_twin_check_passes_for_twins(tmp_prompts):
    _translated_area(tmp_prompts)
    assert prompts.twin_problems() == []


def test_the_twin_check_fails_for_a_text_in_english_only(tmp_prompts):
    _translated_area(tmp_prompts)
    (tmp_prompts / "notify" / "bye.md").write_text("Bye", encoding="utf-8")
    assert prompts.twin_problems() == ["notify/bye has no de twin"]


def test_the_twin_check_fails_for_a_twin_with_no_english_file(tmp_prompts):
    _translated_area(tmp_prompts)
    (tmp_prompts / "notify" / "de" / "bye.md").write_text("Tschüss", encoding="utf-8")
    assert prompts.twin_problems() == ["notify/de/bye has no English file"]


def test_the_twin_check_fails_for_a_twin_with_other_placeholders(tmp_prompts):
    _translated_area(tmp_prompts)
    (tmp_prompts / "notify" / "de" / "hi.md").write_text("Hallo {who}", encoding="utf-8")
    assert prompts.twin_problems() == ["notify/de/hi has {who}, notify/hi has {name}"]


def test_the_twin_check_fails_for_a_twin_of_a_text_that_stays_english(tmp_prompts):
    (tmp_prompts / "talk" / "de").mkdir(parents=True)
    (tmp_prompts / "talk" / "read.md").write_text("Read this", encoding="utf-8")
    (tmp_prompts / "talk" / "de" / "read.md").write_text("Lies das", encoding="utf-8")
    assert prompts.twin_problems() == ["talk/de/read is a twin of a text that stays English"]


def test_load_in_reads_the_twin(tmp_prompts):
    _translated_area(tmp_prompts)
    assert prompts.load_in("de", "notify/hi", name="Bleep") == "Hallo Bleep"
    assert prompts.load_in("en", "notify/hi", name="Bleep") == "Hi Bleep"


def test_the_languages_with_twins_are_the_ones_a_person_can_choose():
    assert get_args(Language) == prompts.LANGUAGES


# --- the fixed texts other parts write (#427) ---


def test_a_shown_text_has_a_twin_and_cannot_be_overridden():
    assert prompts.in_language("shown/notice/back", "de") == "shown/de/notice/back"
    assert not [name for name in prompts.overridable_names() if name.startswith("shown/")]
    with pytest.raises(PromptError):
        prompts.check_override("shown/notice/back", "Back.")


def _shown_area(root: Path) -> None:
    (root / "shown" / "de").mkdir(parents=True)
    for language, hello, done in (
        ("", "Hi {name}: {what}", "done"),
        ("de", "Hallo {name}: {what}", "fertig"),
    ):
        (root / "shown" / language / "hello.md").write_text(hello + "\n", encoding="utf-8")
        (root / "shown" / language / "done.md").write_text(done + "\n", encoding="utf-8")


def test_show_fills_each_template_in_the_language_and_joins_the_parts(tmp_prompts):
    _shown_area(tmp_prompts)
    done = Shown.of("done")
    text = Shown.joined(
        [Shown.of("hello", name="Bleep", what=Shown.joined([done, done], ", ")), done], "; "
    )
    assert prompts.show(text, "en") == "Hi Bleep: done, done; done"
    assert prompts.show(text, "de") == "Hallo Bleep: fertig, fertig; fertig"


def test_show_reads_only_the_shown_texts(tmp_prompts):
    with pytest.raises(PromptError):
        prompts.show(Shown.of("../demo/hello", name="x"), "en")
    with pytest.raises(PromptError):
        prompts.show(Shown.of("hello"), "en")  # demo/hello exists, shown/hello does not
