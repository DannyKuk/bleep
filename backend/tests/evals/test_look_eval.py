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
)
from evals.run import EvalSettings
from fakes import FakeChatModel

HEALTHY = "coolify-healthy"
FAILING = "coolify-url-500"
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


def look(cause: str, plan: dict, read: str | None = None) -> Models:
    """Investigate (optionally reading one container's logs first), Diagnose and Propose."""
    script = []
    if read is not None:
        call = {"name": "container_logs", "args": {"container": read}, "id": "call-1"}
        script.append(reply(tool_calls=[call]))
    diagnosis = {"root_cause": cause, "category": "unknown", "confidence": 0.9}
    script += [reply(f"found: {cause}"), reply(json.dumps(diagnosis)), reply(json.dumps(plan))]
    return Models(script)


def test_every_shipped_case_loads_with_a_healthy_and_a_failing_one():
    cases = load_cases()
    assert {c.id for c in cases} >= {HEALTHY, FAILING}
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
    passed = await run_case(c, settings(), models=look(cause, NO_ACTION))
    assert passed.said_right and passed.passed

    target = c.own()[0]
    restart = {
        "kind": "act",
        "steps": [{"action": "docker.restart", "host_id": "lab", "target": target}],
        "reason": "restart it",
        "verification": {"containers": [target]},
    }
    failed = await run_case(c, settings(), models=look(cause, restart))
    assert failed.forbidden == [f"docker.restart {target}"] and failed.passed is False


async def test_render_scores_stored_runs_again(tmp_path, capsys):
    c = case(HEALTHY)
    result = await run_case(c, settings(), models=look("It is healthy.", NO_ACTION))
    raw = tmp_path / "look.jsonl"
    raw.write_text(result.model_dump_json() + "\n", encoding="utf-8")
    assert read_jsonl(raw)[0].passed

    assert main(["--only", HEALTHY, "--render", str(raw)]) == 0
    out = capsys.readouterr().out
    assert out.strip() == render([c], read_jsonl(raw)).strip()
    assert f"| `{HEALTHY}` | healthy | 1 of 1 |" in out
