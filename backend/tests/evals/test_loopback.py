"""The loop-back eval on its scripted hosts and a scripted model: round one is replayed, the plan
is approved and run on the scripted host, Verify hands over the case's loop-back, and round two
(the model under test) is scored."""

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from bleep.schemas.graph import Diagnosis
from bleep.schemas.settings import ModelSettings
from evals.judge import Judgement, ManualJudge
from evals.loopback import (
    CASES_DIR,
    LoopBackCase,
    LoopBackError,
    apply_grades,
    load_case,
    load_cases,
    main,
    read_jsonl,
    render,
    run_case,
)
from evals.report import Grade, GradeError
from evals.run import EvalSettings
from evals.scripted_host import ScriptedEngine
from fakes import FakeChatModel

FAILED = "verify-failed-oom"
UNPROVEN = "verify-unproven-worker"


def settings() -> EvalSettings:
    models = ModelSettings(provider="local", local_model="tiny-local")
    return EvalSettings(_env_file=None, judge_model="fake-judge", models=models)  # type: ignore[call-arg]


def case(case_id: str) -> LoopBackCase:
    return load_case(CASES_DIR / f"{case_id}.yaml")


def reply(content: str = "", **fields) -> AIMessage:
    usage = {"input_tokens": 1000, "output_tokens": 50, "total_tokens": 1050}
    return AIMessage(content, usage_metadata=usage, **fields)


def round_two(container: str, cause: str, plan: dict) -> list[AIMessage]:
    """Investigate reads one container's logs and answers; Diagnose and Propose answer in JSON."""
    call = {"name": "container_logs", "args": {"container": container}, "id": "call-1"}
    diagnosis = {"root_cause": cause, "category": "resource", "confidence": 0.9}
    return [
        reply(tool_calls=[call]),
        reply(f"found it: {cause}"),
        reply(json.dumps(diagnosis)),
        reply(json.dumps(plan)),
    ]


NO_ACTION = {"kind": "no_action", "reason": "a human must change compose.yaml"}


def restart(target: str) -> dict:
    step = {"action": "docker.restart", "host_id": "lab", "target": target}
    return {"kind": "act", "steps": [step], "reason": "try again", "verification": {}}


class Models:
    def __init__(self, script: list[AIMessage]) -> None:
        self.model = FakeChatModel(script=script, structured_output_unsupported=True)
        self.roles: list[str] = []

    def __call__(self, role: str) -> FakeChatModel:
        self.roles.append(role)
        return self.model


class FakeJudge:
    name = "fake-judge"

    def __init__(self, passed: bool = True) -> None:
        self.passed = passed
        self.calls: list[tuple[str, Diagnosis]] = []

    async def judge(self, expected: str, diagnosis: Diagnosis) -> Judgement:
        self.calls.append((expected, diagnosis))
        score = 0.9 if self.passed else 0.3
        return Judgement(score=score, passed=self.passed, reason="graded", usd=0.001)


def engine_for(c: LoopBackCase) -> ScriptedEngine:
    return ScriptedEngine("lab", c.service, c.containers, c.events)


def test_every_shipped_case_loads():
    cases = load_cases()
    assert {c.id for c in cases} >= {FAILED, UNPROVEN}
    assert {c.loop_back for c in cases} == {"verify_failed", "verify_unproven"}


async def test_a_failed_fix_loops_back_with_verify_failed_and_round_two_is_scored():
    c = case(FAILED)
    engine = engine_for(c)
    models = Models(round_two("shop-web", "the 128 MiB limit is too small", NO_ACTION))
    judge = FakeJudge()

    result = await run_case(c, settings(), judge=judge, models=models, engine=engine)

    assert result.run.error is None, result.run.error
    assert engine.acts == [("restart", "shop-web")]  # round one's approved plan, and only it
    assert result.loop_back == "verify_failed"
    assert "container shop-web: is exited" in result.checks
    assert any(check.startswith("url http://shop.lab:8080/health") for check in result.checks)
    # Only round two reached the model under test: Investigate, Diagnose and Propose.
    assert models.model.cursor == 4
    seen = "\n".join(str(m.content) for m in models.model.seen[0])
    assert "the check afterwards failed" in seen  # verify_failed.md reached Investigate
    assert c.round_one.findings[:40] in seen  # round one's replayed answer is in the history
    assert [call.name for call in result.tool_calls] == ["container_logs"]
    assert result.run.diagnosis is not None
    assert result.run.diagnosis.root_cause == "the 128 MiB limit is too small"
    assert judge.calls == [(c.expected.root_cause, result.run.diagnosis)]
    assert result.run.action_correct and result.run.category_correct
    assert result.repeated_fix == [] and result.passed is True
    assert result.run.tokens == 4 * 1050


async def test_proposing_the_failed_fix_again_fails_even_with_the_right_root_cause():
    c = case(FAILED)
    models = Models(round_two("shop-web", "out of memory", restart("shop-web")))

    result = await run_case(c, settings(), judge=FakeJudge(), models=models, engine=engine_for(c))

    assert result.run.error is None, result.run.error
    assert result.repeated_fix == ["docker.restart shop-web"]
    assert result.run.unnecessary_action and result.run.forbidden == ["docker.restart shop-web"]
    assert result.passed is False


async def test_an_approved_stop_loops_back_with_verify_unproven():
    c = case(UNPROVEN)
    engine = engine_for(c)
    models = Models(round_two("orders-api", "orders-api still takes orders", NO_ACTION))

    result = await run_case(c, settings(), judge=FakeJudge(), models=models, engine=engine)

    assert result.run.error is None, result.run.error
    assert engine.acts == [("stop", "orders-worker")]
    assert result.loop_back == "verify_unproven"
    assert all("nothing shows the service works" in check for check in result.checks)
    seen = "\n".join(str(m.content) for m in models.model.seen[0])
    assert "not a reason to start a stopped container again" in seen
    assert result.evidence_reads == ["container_logs orders-api"]
    assert result.started_stopped == [] and result.passed is True


async def test_reading_only_the_stopped_container_finds_no_evidence():
    c = case(UNPROVEN)
    models = Models(round_two("orders-worker", "the worker crashed", NO_ACTION))

    result = await run_case(c, settings(), judge=FakeJudge(), models=models, engine=engine_for(c))

    assert result.evidence_reads == [] and result.passed is False


async def test_starting_the_stopped_container_again_fails_the_unproven_case():
    c = case(UNPROVEN)
    start = {
        "kind": "act",
        "steps": [{"action": "docker.start", "host_id": "lab", "target": "orders-worker"}],
        "reason": "bring the worker back",
        "verification": {},
    }
    models = Models(round_two("orders-api", "the worker is down", start))

    result = await run_case(c, settings(), judge=FakeJudge(), models=models, engine=engine_for(c))

    assert result.run.error is None, result.run.error
    assert result.evidence_reads == ["container_logs orders-api"]
    assert result.started_stopped == ["docker.start orders-worker"]
    assert result.passed is False


async def test_a_host_that_lets_verify_pass_is_a_failed_run_not_a_score():
    c = case(FAILED)
    shop_web = next(x for x in c.containers if x.name == "shop-web")
    shop_web.after = None  # the restart works: shop-web comes back healthy
    answers = {"http://shop.lab:8080/health": {"status": 200, "body": '{"status":"ok"}'}}
    c = LoopBackCase.model_validate(c.model_dump() | {"url_answers": answers})
    models = Models([])  # round two never runs

    result = await run_case(c, settings(), judge=FakeJudge(), models=models, engine=engine_for(c))

    assert result.run.failure == "error"
    assert "did not make Verify loop back" in (result.run.error or "")
    assert result.passed is None and models.model.cursor == 0


async def test_a_case_whose_loop_back_is_the_other_one_is_a_failed_run():
    c = case(UNPROVEN).model_copy(update={"loop_back": "verify_failed"})
    models = Models(round_two("orders-api", "fine", NO_ACTION))

    result = await run_case(c, settings(), judge=FakeJudge(), models=models, engine=engine_for(c))

    assert result.run.failure == "error"
    assert "handed over verify_unproven" in (result.run.error or "")


async def test_with_the_manual_judge_a_failed_case_waits_for_its_grade(tmp_path):
    c = case(FAILED)
    models = Models(round_two("shop-web", "memory limit", NO_ACTION))
    result = await run_case(c, settings(), judge=ManualJudge(), models=models, engine=engine_for(c))
    assert result.grade_pending and result.passed is None

    grade = Grade(run_id=f"{FAILED}/1", root_cause_pass=True, root_cause_score=0.9, note="limit")
    [graded] = apply_grades([result], [grade])

    assert graded.passed is True and graded.run.judge == "manual"
    assert "limit" in render([c], [graded])
    with pytest.raises(GradeError):
        apply_grades([result], [grade.model_copy(update={"run_id": "other/1"})])


def test_a_case_naming_a_container_the_host_lacks_is_refused(tmp_path):
    data = case(UNPROVEN).model_dump(mode="json")
    data["round_one"]["plan"]["steps"][0]["target"] = "orders-mailer"
    path = tmp_path / f"{UNPROVEN}.yaml"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(LoopBackError, match="orders-mailer"):
        load_case(path)


def test_an_unknown_case_is_refused(capsys):
    assert main(["--only", "nope"]) == 2
    assert "no case nope" in capsys.readouterr().err


async def test_render_reads_back_what_a_run_wrote(tmp_path: Path, capsys):
    c = case(UNPROVEN)
    models = Models(round_two("orders-api", "orders-api takes orders", NO_ACTION))
    result = await run_case(c, settings(), judge=FakeJudge(), models=models, engine=engine_for(c))
    raw = tmp_path / "loopback.jsonl"
    raw.write_text(result.model_dump_json() + "\n", encoding="utf-8")

    assert read_jsonl(raw) == [result]
    assert main(["--only", UNPROVEN, "--render", str(raw)]) == 0
    tables = capsys.readouterr().out
    assert f"| `{UNPROVEN}` | verify_unproven | 1 of 1 |" in tables
