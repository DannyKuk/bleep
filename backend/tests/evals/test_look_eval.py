"""The look eval on its scripted hosts and a scripted model: a look opens the incident, Bleep's
graph runs through Propose, and the run is scored in code on what the model was given and what
it said (#423)."""

import json

import pytest
from langchain_core.messages import AIMessage

from bleep.schemas.settings import ModelSettings
from evals.look import (
    CASES_DIR,
    LookCase,
    LookError,
    line,
    load_case,
    load_cases,
    main,
    read_jsonl,
    render,
    run_case,
    score,
)
from evals.run import EvalSettings
from fakes import FakeChatModel

HEALTHY = "coolify-healthy"
NO_URL = "coolify-healthy-no-url"
FAILING = "coolify-url-500"
NO_URL_FAILING = "coolify-nourl-log-errors"
NO_ACTION = {"kind": "no_action", "reason": "nothing to change"}


def settings() -> EvalSettings:
    models = ModelSettings(provider="local", local_model="tiny-local")
    return EvalSettings(_env_file=None, judge_model="fake-judge", models=models)  # type: ignore[call-arg]


def case(case_id: str) -> LookCase:
    return load_case(CASES_DIR / f"{case_id}.yaml")


def reply(content: str = "", **fields) -> AIMessage:
    usage = {"input_tokens": 1000, "output_tokens": 50, "total_tokens": 1050}
    return AIMessage(content, usage_metadata=usage, **fields)


class Models:
    def __init__(self, script: list[AIMessage]) -> None:
        self.model = FakeChatModel(script=script, structured_output_unsupported=True)

    def __call__(self, role: str) -> FakeChatModel:
        return self.model


def look(cause: str, plan: dict, read: str | None = None, category: str = "unknown") -> Models:
    """Investigate (optionally reading one container's logs first), Diagnose and Propose."""
    script = []
    if read is not None:
        call = {"name": "container_logs", "args": {"container": read}, "id": "call-1"}
        script.append(reply(tool_calls=[call]))
    diagnosis = {"root_cause": cause, "category": category, "confidence": 0.9}
    script += [reply(f"found: {cause}"), reply(json.dumps(diagnosis)), reply(json.dumps(plan))]
    return Models(script)


def test_every_shipped_case_loads_with_a_healthy_and_a_failing_one():
    cases = load_cases()
    assert {c.id for c in cases} >= {HEALTHY, NO_URL, FAILING, NO_URL_FAILING}
    assert {c.healthy for c in cases} == {True, False}
    for c in cases:
        assert "coolify-proxy" not in c.own()


def test_a_case_without_a_container_outside_the_service_is_refused(tmp_path):
    data = case(HEALTHY).model_dump(mode="json")
    data["containers"] = [c for c in data["containers"] if c["project"] is None] * 2
    (tmp_path / "x.yaml").write_text(json.dumps(data | {"id": "x"}), encoding="utf-8")
    with pytest.raises(LookError, match="outside the service"):
        load_case(tmp_path / "x.yaml")


async def test_a_healthy_look_that_was_given_its_logs_and_url_and_says_so_passes():
    c = case(HEALTHY)
    models = look("Nothing is wrong: the app is healthy and its URL answers.", NO_ACTION)

    result = await run_case(c, settings(), models=models)

    assert result.failure is None, result.error
    assert result.gathered_logs == c.own()  # Gather read the service's own container, only
    assert result.checked_urls == ["https://dkwebsite.example/"]
    assert result.unread_logs == [] and result.unchecked_urls == []
    assert result.said_right and result.passed
    assert "passed" in line(result)


async def test_a_failing_look_called_healthy_after_reading_the_proxy_fails():
    c = case(FAILING)
    models = look("Nothing is wrong: the proxy log is quiet.", NO_ACTION, read="coolify-proxy")

    result = await run_case(c, settings(), models=models)

    assert result.outside_reads == ["container_logs coolify-proxy"]
    assert result.said_right is False and result.passed is False
    assert "root cause says the wrong thing" in line(result)


async def test_a_failing_look_that_names_the_failure_passes_unless_it_restarts():
    c = case(FAILING)
    cause = "The home page answers 500: an unhandled error reading 'slug' in the posts API."
    passed = await run_case(c, settings(), models=look(cause, NO_ACTION, category="code"))
    assert passed.said_right and passed.passed

    target = c.own()[0]
    restart = {
        "kind": "act",
        "steps": [{"action": "docker.restart", "host_id": "lab", "target": target}],
        "reason": "restart it",
        "verification": {"containers": [target]},
    }
    failed = await run_case(c, settings(), models=look(cause, restart, category="code"))
    assert failed.forbidden == [f"docker.restart {target}"] and failed.passed is False


async def test_render_scores_stored_runs_again(tmp_path, capsys):
    c = case(HEALTHY)
    result = await run_case(c, settings(), models=look("It is healthy.", NO_ACTION))
    assert result.passed
    raw = tmp_path / "look.jsonl"
    raw.write_text(result.model_dump_json() + "\n", encoding="utf-8")
    assert read_jsonl(raw)[0].passed

    assert main(["--only", HEALTHY, "--render", str(raw)]) == 0
    out = capsys.readouterr().out
    assert out.strip() == render([c], read_jsonl(raw)).strip()
    assert f"| `{HEALTHY}` | healthy | 1 of 1 |" in out


@pytest.mark.parametrize(
    ("case_id", "cause", "category"),
    [
        # The URL check's own line gives "500"; calling it transient is calling the app healthy.
        (
            FAILING,
            "The service is healthy; the single 500 from the URL check is transient.",
            "unknown",
        ),
        (FAILING, "The home page answers 500 now and then.", "unknown"),  # healthy's category
        # A false alarm on the healthy app: its h3 deprecation warnings read as a fault.
        (
            HEALTHY,
            "The container is healthy, but its log shows repeated h3 statusMessage errors, "
            "a code fault.",
            "code",
        ),
        (HEALTHY, "Nothing is wrong: the app is healthy and its URL answers.", "code"),
    ],
)
async def test_a_wrong_answer_fails(case_id, cause, category):
    result = await run_case(
        case(case_id), settings(), models=look(cause, NO_ACTION, category=category)
    )
    assert result.failure is None, result.error
    assert result.said_right is False and result.passed is False


async def test_a_healthy_answer_that_says_no_fault_or_no_errors_is_not_a_false_alarm():
    cause = "Nothing is wrong: no fault and no errors in its own log, and its URL answers."
    result = await run_case(case(HEALTHY), settings(), models=look(cause, NO_ACTION))
    assert result.said_right and result.passed


async def test_a_look_at_a_service_without_urls_can_pass_on_its_logs_alone():
    c = case(NO_URL)
    assert c.urls == []
    result = await run_case(
        c, settings(), models=look("It is healthy: its log shows it listening.", NO_ACTION)
    )
    assert result.checked_urls == [] and result.unchecked_urls == []
    assert result.passed


async def test_a_run_that_ends_early_fails():
    c = case(HEALTHY)
    result = await run_case(c, settings(), models=Models([]))  # the model has nothing to say
    assert result.failure is not None or result.diagnosis is None
    assert result.passed is False
    assert "FAILED" in line(result)
    assert f"| `{HEALTHY}` | healthy | 0 of 1 |" in render([c], [result])


@pytest.mark.parametrize("case_id", [HEALTHY, NO_URL])
@pytest.mark.parametrize(
    "cause",
    [
        "Missing a URL check; cannot say it is healthy.",
        "The container is healthy, but the app has a failure in its h3 handler that needs fixing.",
        "The container is healthy, but the h3 handler is broken.",
        "It is healthy, though its health could not be confirmed.",
    ],
)
async def test_a_healthy_case_fails_an_unsure_or_alarmed_answer(case_id, cause):
    result = await run_case(case(case_id), settings(), models=look(cause, NO_ACTION))
    assert result.failure is None, result.error
    assert result.said_right is False and result.passed is False


@pytest.mark.parametrize("case_id", [HEALTHY, NO_URL])
async def test_a_healthy_case_passes_no_problem_and_no_issue(case_id):
    cause = "Nothing is wrong: no problem and no issues in its own log; the app is healthy."
    result = await run_case(case(case_id), settings(), models=look(cause, NO_ACTION))
    assert result.said_right and result.passed


async def test_a_healthy_case_fails_a_diagnosis_marked_unsure():
    c = case(NO_URL)
    result = await run_case(c, settings(), models=look("It is healthy.", NO_ACTION))
    assert result.passed and result.diagnosis is not None
    diagnosis = result.diagnosis.model_copy(update={"unsure": True})
    assert score(c, result.model_copy(update={"diagnosis": diagnosis})).passed is False


@pytest.mark.parametrize(
    "cause",
    [
        "The posts API fails with an unhandled error, though the container looks healthy.",
        "Every page answers 500 on an unhandled error reading 'slug', not a transient blip.",
    ],
)
async def test_a_failing_case_passes_a_right_answer_that_mentions_the_healthy_container(cause):
    models = look(cause, NO_ACTION, category="code")
    result = await run_case(case(FAILING), settings(), models=models)
    assert result.said_right and result.passed


async def test_a_failing_look_without_urls_that_names_the_log_errors_passes():
    c = case(NO_URL_FAILING)
    assert c.urls == [] and not c.healthy
    cause = "The container is healthy, but its own log shows an unhandled error reading 'slug'."
    result = await run_case(c, settings(), models=look(cause, NO_ACTION, category="code"))
    assert result.checked_urls == [] and result.unchecked_urls == []
    assert result.gathered_logs == c.own()  # the own log is what shows the failure
    assert result.said_right and result.passed


@pytest.mark.parametrize(
    ("cause", "category"),
    [
        # What the no-URL line must not cause: the containers run, so the service is called healthy.
        ("Nothing is wrong: its containers run and it has no URL to check.", "unknown"),
        ("The app is healthy: its container runs and its health rests on its logs.", "unknown"),
        # Right category, wrong verdict.
        ("The app is healthy and working normally; the unhandled errors are noise.", "code"),
        # Right verdict in the wrong category.
        ("Its own log shows an unhandled error reading 'slug' in the posts API.", "unknown"),
    ],
)
async def test_a_failing_look_without_urls_called_healthy_on_its_containers_fails(cause, category):
    models = look(cause, NO_ACTION, category=category)
    result = await run_case(case(NO_URL_FAILING), settings(), models=models)
    assert result.failure is None, result.error
    assert result.said_right is False and result.passed is False


async def test_a_failing_look_without_urls_that_restarts_the_container_fails():
    c = case(NO_URL_FAILING)
    target = c.own()[0]
    restart = {
        "kind": "act",
        "steps": [{"action": "docker.restart", "host_id": "lab", "target": target}],
        "reason": "restart it",
        "verification": {"containers": [target]},
    }
    cause = "An unhandled error reading 'slug' in the posts API."
    result = await run_case(c, settings(), models=look(cause, restart, category="code"))
    assert result.forbidden == [f"docker.restart {target}"] and result.passed is False
