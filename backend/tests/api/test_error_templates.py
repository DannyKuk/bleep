"""The templates the API's errors are said with (#456): each one in English reads as the text it
replaced, each has a German twin, and what an error stores or logs stays English."""

import ast
import re
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_core import PydanticCustomError, PydanticKnownError

from bleep import prompts
from bleep.api.errors import Refusal, problem_text, render
from bleep.guard.sanitize import MAX_LINE
from bleep.hosts.dashboard import HostConflict, HostKeyBusy, HostKeyUnavailable, HostRefused
from bleep.hosts.ssh import AddressRefused, vetted_address
from bleep.incidents.prompt_overrides import PromptRefused, check_lines
from bleep.incidents.settings_store import SettingsRefused, check_body
from bleep.plugins.urls import NotPublic, UrlRefused, check_dashboard_url, check_public, check_url
from bleep.schemas.refusal import Problem
from bleep.store.secrets import MAX_VALUE_CHARS, _checked

SRC = Path(__file__).resolve().parents[2] / "src" / "bleep"
API_DIR = prompts.PROMPTS_DIR / "api"


def said(error: Exception, language: str) -> str:
    return render(
        getattr(error, "template", ""), getattr(error, "values", {}), str(error), language
    )


def refused(call, *args, **kwargs) -> Exception:
    with pytest.raises(Exception) as caught:
        call(*args, **kwargs)
    return caught.value


# --- the errors other parts raise ---


async def vetted_refusal(host: str, resolve=None) -> Exception:
    with pytest.raises(AddressRefused) as caught:
        await vetted_address(host, 22, resolve)
    return caught.value


async def public_refusal(resolver) -> Exception:
    with pytest.raises(NotPublic) as caught:
        await check_public("example.com", 443, resolver)
    return caught.value


async def nothing(host, port):
    return []


async def unreachable(host, port):
    raise OSError("no")


async def private(host, port):
    return ["10.0.0.5"]


async def textual_cases() -> list[Exception]:
    return [
        refused(_checked, "GITHUB_TOKEN", ""),
        refused(_checked, "GITHUB_TOKEN", "x" * (MAX_VALUE_CHARS + 1)),
        refused(_checked, "GITHUB_TOKEN", "a\nb"),
        refused(_checked, "GITHUB_TOKEN", "a\x00b", "multiline"),
        refused(check_url, "ftp://example.com", "X_URL"),
        refused(check_url, "https://example.com/?q=1", "X_URL"),
        refused(check_url, "http://metadata.google.internal", "X_URL", internal=True),
        refused(check_url, "http://example.com", "X_URL"),
        refused(
            check_url,
            "http://example.com",
            "X_URL",
            https_hint="; use Coolify's https domain",
            hint_name="coolify",
        ),
        refused(check_dashboard_url, "http://10.0.0.5", "X_URL"),
        refused(check_lines, "a\rb"),
        refused(check_lines, "x" * (MAX_LINE + 1)),
        refused(check_body, {"general": {"api_key": "x"}}),
        await vetted_refusal("127.0.0.1"),
        await vetted_refusal("box.example", unreachable),
        await public_refusal(unreachable),
        await public_refusal(nothing),
        await public_refusal(private),
        HostKeyBusy(),
        HostKeyUnavailable(),
        HostConflict("host a exists already", "host_exists", host="a"),
    ]


async def test_an_error_in_english_reads_as_its_own_text_and_in_german_differs():
    for error in await textual_cases():
        assert error.template, str(error)
        assert said(error, "en") == str(error)
        assert said(error, "de") != str(error)


async def test_what_an_error_stores_or_logs_stays_english_whatever_the_language():
    for error in await textual_cases():
        before = str(error)
        said(error, "de")
        assert str(error) == before
        assert re.fullmatch(r"[\x00-\x7f]+", before), before


def test_an_override_the_loader_refuses_is_said_in_german_with_the_placeholders():
    name = next(
        n
        for n in prompts.overridable_names()
        if len(prompts.placeholders(prompts.file_text(n))) == 1
    )
    (needed,) = prompts.placeholders(prompts.file_text(name))
    both = refused(prompts.check_override, name, "{zzz}")
    missing = refused(prompts.check_override, name, "nothing")
    unknown = refused(prompts.check_override, name, f"{{{needed}}} {{zzz}}")
    control = refused(prompts.check_override, name, f"{{{needed}}}\x00")

    for error in (both, missing, unknown, control):
        assert said(error, "en") == str(error)
    assert said(both, "de") == (
        f"Verwende genau die Platzhalter der Datei; es fehlen: {{{needed}}}; unbekannt: {{zzz}}"
    )
    assert said(missing, "de").endswith("unbekannt: keine")
    assert said(unknown, "de").endswith("es fehlen: keine; unbekannt: {zzz}")
    assert (
        said(control, "de")
        == "Steuerzeichen, Bidi-Zeichen und unsichtbare Zeichen sind nicht erlaubt"
    )


def test_a_refusal_carried_inside_another_is_said_in_the_same_language():
    inner = UrlRefused("X_URL is not a URL", "url_not_a_url", name="X_URL")
    outer = Refusal(422, "plugin_url", plugin="coolify", reason=inner)
    assert outer.text_in("en") == "plugin coolify: X_URL is not a URL"
    assert outer.text_in("de") == "Plugin coolify: X_URL ist keine URL"


def test_a_validation_reason_inside_a_refusal_is_said_in_the_picked_language():
    problems = (
        Problem(
            ("a", "b"), "less_than_equal", "Input should be less than or equal to 5", {"le": "5"}
        ),
    )
    error = SettingsRefused("host a: a.b: x", "settings_host_reasons", host="a", reasons=problems)
    assert said(error, "en") == "host a: a.b: Input should be less than or equal to 5"
    assert said(error, "de") == "Host a: a.b: Die Eingabe darf höchstens 5 sein"


def test_a_count_in_a_refusal_takes_the_languages_thousands_separator():
    error = HostRefused("x")
    assert said(error, "de") == "x"  # no template: the error's own text
    over = PromptRefused(
        "a line is over 4,000 characters; break it up", "prompt_line_long", limit=4000
    )
    assert said(over, "de") == "Eine Zeile hat mehr als 4.000 Zeichen; teile sie auf"
    assert said(over, "en") == "a line is over 4,000 characters; break it up"


def test_a_template_that_no_longer_renders_falls_back_to_the_errors_own_text():
    broken = PromptRefused("the English", "no_such_template", unused="x")
    assert said(broken, "de") == "the English"
    wrong_values = PromptRefused("the English", "host_exists", other="a")
    assert said(wrong_values, "de") == "the English"


# --- pydantic's types ---

CUSTOM = frozenset(
    {
        "address_scope",
        "address_shape",
        "login_shape",
        "socket_path",
        "short_name",
        "one_plain_line",
        "url_check_invalid",
        "time_zone",
        "plain_url",
        "looks_like_key",
        "instruction_needs_text",
        "fact_needs_text",
        "exits_star_alone",
        "exits_names",
        "exits_too_many",
        "plain_text_line",
        "tool_read_and_act",
        "quiet_leave_alone_until",
        "quiet_until_needed",
        "quiet_until_zone",
        "quiet_until_past",
        "quiet_until_far",
    }
)
SAMPLES = {
    "string_too_short": {"min_length": 3},
    "string_too_short_one": {"min_length": 1},
    "string_too_long": {"max_length": 3},
    "string_too_long_one": {"max_length": 1},
    "string_pattern_mismatch": {"pattern": "^a$"},
    "greater_than": {"gt": 5},
    "greater_than_equal": {"ge": 5},
    "less_than": {"lt": 5},
    "less_than_equal": {"le": 5},
    "multiple_of": {"multiple_of": 5},
    "too_short": {"field_type": "List", "min_length": 3, "actual_length": 1},
    "too_short_one": {"field_type": "List", "min_length": 1, "actual_length": 0},
    "too_long": {"field_type": "List", "max_length": 3, "actual_length": 5},
    "too_long_one": {"field_type": "List", "max_length": 1, "actual_length": 2},
    "literal_error": {"expected": "'a'"},
    "model_type": {"class_name": "Thing"},
    "datetime_parsing": {"error": "invalid character"},
    "datetime_from_date_parsing": {"error": "invalid character"},
    "url_parsing": {"error": "empty host"},
    "enum": {"expected": "'a'"},
}
PROBLEM_NAMES = sorted(
    path.stem for path in (API_DIR / "problem").glob("*.md") if path.stem not in CUSTOM
)


@pytest.mark.parametrize("name", PROBLEM_NAMES)
def test_a_pydantic_type_in_english_reads_as_pydantics_own_message(name):
    ctx = SAMPLES.get(name, {})
    kind = name.removesuffix("_one") if name.endswith("_one") else name
    msg = PydanticKnownError(kind, ctx or None).message()
    problem = Problem(("f",), kind, msg, {key: str(value) for key, value in ctx.items()})

    assert problem_text(problem, "en") == msg
    german = problem_text(problem, "de")
    assert german != msg
    assert "{" not in german


def test_a_type_with_no_template_or_a_context_that_does_not_fit_keeps_pydantics_message():
    unknown = Problem(("f",), "made_up_type", "Made up", {})
    short_of_context = Problem(("f",), "less_than_equal", "Input should be less than 5", {})
    assert problem_text(unknown, "de") == "Made up"
    assert problem_text(short_of_context, "de") == "Input should be less than 5"


def trigger(call, *args) -> PydanticCustomError:
    with pytest.raises(PydanticCustomError) as caught:
        call(*args)
    return caught.value


def custom_errors() -> list[PydanticCustomError]:
    from bleep.hosts.config import MAX_EXITS, check_exits
    from bleep.schemas import dashboard_hosts as hosts
    from bleep.schemas import settings as settings_schema

    errors = [
        trigger(hosts._address, "a%b"),
        trigger(hosts._address, "-bad-"),
        trigger(hosts._user, "-x"),
        trigger(hosts._docker_socket, "relative"),
        trigger(hosts._name, ""),
        trigger(settings_schema._one_plain_line, "a\nb"),
        trigger(settings_schema._known_zone, "Mars/Base"),
        trigger(settings_schema._plain_url, "ftp://x"),
        trigger(settings_schema._not_a_key, "sk-" + "a" * 30),
        trigger(check_exits, ["*", "job"]),
        trigger(check_exits, ["!!"]),
        trigger(check_exits, [f"job{i}" for i in range(MAX_EXITS + 1)]),
    ]
    from bleep.schemas.api import FactBody

    with pytest.raises(ValidationError) as fact:
        FactBody(text="  ")
    from bleep.plugins.mcp_url import McpSettings

    with pytest.raises(ValidationError) as tools:
        McpSettings(read_tools=["a"], act_tools=["a"])
    with pytest.raises(ValidationError) as line:
        McpSettings(purpose="a\nb")
    for caught in (fact, tools, line):
        (item,) = caught.value.errors(include_input=False, include_url=False)
        errors.append(PydanticCustomError(item["type"], item["msg"], item.get("ctx")))
    return errors


def test_each_validator_of_bleeps_own_has_a_template_in_english_and_german():
    errors = custom_errors()
    assert {error.type for error in errors} <= CUSTOM
    for error in errors:
        problem = Problem(
            ("f",),
            error.type,
            error.message(),
            {key: str(value) for key, value in (error.context or {}).items()},
        )
        assert problem_text(problem, "en") == error.message()
        assert problem_text(problem, "de") != error.message()


def test_every_custom_type_and_template_the_code_names_has_a_file():
    types = {path.stem for path in (API_DIR / "problem").glob("*.md")}
    assert types >= CUSTOM


# --- the names in the code and the files agree ---

TEMPLATED = {
    "Refusal": 1,
    "_conflict": 0,
    "stale": 1,
    "SecretRefused": 1,
    "SecretNotSaved": 1,
    "AddressStale": 1,
    "SettingsRefused": 1,
    "PluginError": 1,
    "UrlRefused": 1,
    "refused": 1,
    "HostRefused": 1,
    "HostConflict": 1,
    "PromptRefused": 1,
    "PromptError": 1,
    "AddressRefused": 1,
    "NotPublic": 1,
    "McpUrlRefused": 1,
}
# Named from a value at run time (`incident_{status}`, `rate_no_{target}`, `url_https_{hint}`) or
# by the body limit, which renders it without raising.
BUILT = {
    "body_too_large_bytes",
    "host_key_busy",
    "host_key_unavailable",
    "incident_resolved",
    "incident_closed",
    "incident_failed",
    "rate_no_diagnosis",
    "rate_no_proposal",
    "url_https_mcp",
    "url_https_coolify",
    "secrets_from_env",
    "secret_from_env",
    "secret_control",
    "secret_line_break",
    "prompt_placeholders_missing",
    "prompt_placeholders_unknown",
}


def templates_named_in_the_code() -> set[str]:
    named: set[str] = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            at = TEMPLATED.get(name)
            if at is None or len(node.args) <= at:
                continue
            arg = node.args[at]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                named.add(arg.value)
            elif isinstance(arg, ast.IfExp):
                named |= {
                    branch.value
                    for branch in (arg.body, arg.orelse)
                    if isinstance(branch, ast.Constant) and isinstance(branch.value, str)
                }
    return named


def test_every_template_the_code_names_has_a_file_and_every_file_is_named():
    files = {path.stem for path in API_DIR.glob("*.md") if path.stem != "README"}
    named = templates_named_in_the_code() | BUILT
    named -= {"invalid"}  # `Refusal.invalid` lists problems and has no text of its own
    assert {n for n in named if n and n not in files} == set()
    assert files - named == set(), "a template nothing names"


def test_the_api_texts_are_not_overridable_and_no_model_reads_them():
    names = {
        p.relative_to(prompts.PROMPTS_DIR).with_suffix("").as_posix()
        for p in prompts.all_prompt_files()
    }
    api = {name for name in names if name.startswith("api/")}
    assert api
    assert not api & set(prompts.overridable_names())
    for path in (SRC / "graph").rglob("*.py"):
        assert "api/" not in path.read_text(encoding="utf-8").replace("api/v1", ""), path
