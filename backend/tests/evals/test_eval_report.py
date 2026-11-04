"""The dated report and the raw JSONL a run writes."""

import json
from datetime import UTC, date, datetime

import pytest

from bleep.schemas.graph import Diagnosis, PlanStep, Proposal
from evals.report import (
    GradeError,
    RunInfo,
    append_jsonl,
    apply_grades,
    main,
    read_grades,
    read_info,
    read_jsonl,
    render,
    report_path,
    write_info,
)
from evals.scenarios import Scenario, ScenarioResult

SCENARIO = Scenario.model_validate(
    {
        "id": "db-down",
        "fault": "db-down",
        "subject": {"kind": "service", "host_id": "demo", "service_id": "demo/compose:shop"},
        "expected": {
            "root_cause": "shop-db is stopped",
            "category": "dependency",
            "action": {"kind": "act", "steps": [{"action": "docker.start", "target": "shop-db"}]},
        },
    }
)
INFO = RunInfo(
    started_at=datetime(2026, 11, 3, 14, 0, tzinfo=UTC),
    finished_at=datetime(2026, 11, 3, 14, 20, tzinfo=UTC),
    model="huihui-qwen3.8-27b-abliterated",
    provider="local",
    judge="openai/gpt-6-luna",
    repeat=2,
    scenarios=[SCENARIO],
    commit="abc1234",
    command="uv run --group evals python -m evals.run --repeat 2",
    raw="data/evals/scenarios-20261103-140000.jsonl",
)


def result(run: int, **fields) -> ScenarioResult:
    base = {
        "id": "db-down",
        "run": run,
        "model": INFO.model,
        "judge": INFO.judge,
        "diagnosis": Diagnosis(root_cause="db | stopped", category="dependency", confidence=0.8),
        "proposal": Proposal(
            kind="act",
            steps=[PlanStep(action="docker.start", host_id="demo", target="shop-db")],
            reason="start it",
        ),
        "root_cause_score": 0.9,
        "root_cause_pass": True,
        "judge_reason": "names it",
        "category_correct": True,
        "action_correct": True,
        "steps": 4,
        "tokens": 10_000,
        "peak_prompt_tokens": 6_000,
        "usd": 0.0,
        "judge_usd": 0.001,
        "seconds_to_incident": 70.0,
        "seconds_to_diagnosis": 130.0,
    }
    return ScenarioResult(**(base | fields))


RESULTS = [
    result(1),
    result(
        2,
        root_cause_score=0.3,
        root_cause_pass=False,
        action_correct=False,
        unnecessary_action=True,
        forbidden=["docker.restart shop-web"],
        peak_prompt_tokens=9_500,
        steps=8,
        judge_usd=0.002,
    ),
]


def test_the_report_is_named_by_the_day_it_ran(tmp_path):
    assert report_path(date(2026, 11, 3), tmp_path) == tmp_path / "scenarios-2026-11-03.md"


def test_a_second_report_on_the_same_day_gets_a_suffix(tmp_path):
    day = date(2026, 11, 3)
    first = report_path(day, tmp_path)
    first.write_text("earlier", encoding="utf-8")
    second = report_path(day, tmp_path)
    assert second.name == "scenarios-2026-11-03-2.md"


def test_the_report_has_setup_results_totals_cost_and_what_it_shows():
    text = render(INFO, RESULTS)
    for heading in ("## Setup", "## db-down", "## Totals", "## Cost", "## What it shows"):
        assert heading in text
    assert "`huihui-qwen3.8-27b-abliterated`" in text and "`openai/gpt-6-luna`" in text
    assert INFO.command in text and INFO.raw in text
    assert "1 of 2" in text  # root cause passes and exact plans
    assert "9,500" in text  # the largest prompt
    assert "docker.restart shop-web" in text
    assert "$0.0030" in text  # the judge's cost over both runs


def test_text_from_a_run_cannot_break_a_table():
    text = render(INFO, RESULTS)
    assert "db \\| stopped" in text


def test_a_run_that_ended_early_is_listed_with_its_error():
    failed = result(3, diagnosis=None, proposal=None, root_cause_score=None,
                    root_cause_pass=False, error="timed out waiting for the incident")  # fmt: skip
    text = render(INFO, [*RESULTS, failed])
    assert "timed out waiting for the incident" in text


def test_runs_out_of_context_are_failed_runs_counted_in_the_summary():
    cut = result(3, diagnosis=None, proposal=None, root_cause_score=None,
                 root_cause_pass=False, failure="context_length",
                 error="LengthFinishReasonError: length limit")  # fmt: skip
    timed_out = result(4, diagnosis=None, proposal=None, root_cause_score=None,
                       root_cause_pass=False, failure="timeout", error="timed out")  # fmt: skip
    text = render(INFO, [*RESULTS, cut, timed_out])
    assert "- Run 3 failed (context_length): LengthFinishReasonError" in text
    assert "1 run ran out of context." in text.split("## Setup")[0]
    totals = text.split("## Totals")[1].split("## Cost")[0]
    rows = [line for line in totals.splitlines() if line.startswith(("| db-down", "| **All**"))]
    assert len(rows) == 2
    for row in rows:  # Failed, then Out of context, after Unnecessary
        assert row.split(" | ")[6:8] == ["2", "1"]
    assert "1 run failed with `context_length`" in text
    assert "ran out of context" not in render(INFO, RESULTS)


def test_results_round_trip_through_the_jsonl(tmp_path):
    path = tmp_path / "data" / "run.jsonl"
    for r in RESULTS:
        append_jsonl(path, r)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
    assert read_jsonl(path) == RESULTS


def test_a_run_is_named_by_its_scenario_and_number():
    assert result(2).run_id == "db-down/2"


def grades_file(tmp_path, *grades: dict) -> object:
    path = tmp_path / "grades.jsonl"
    path.write_text("".join(json.dumps(g) + "\n" for g in grades), encoding="utf-8")
    return path


def test_grades_by_hand_set_each_runs_root_cause(tmp_path):
    blank = {"root_cause_score": None, "root_cause_pass": False, "judge_reason": None}
    pending = [r.model_copy(update=blank | {"judge": "manual"}) for r in RESULTS]
    path = grades_file(
        tmp_path,
        {"run_id": "db-down/2", "root_cause_pass": True, "root_cause_score": 0.9, "note": "right"},
        {"run_id": "db-down/1", "root_cause_pass": False, "root_cause_score": 0.2, "note": "web"},
    )
    graded = apply_grades(pending, read_grades(path))
    assert [(r.root_cause_score, r.root_cause_pass, r.judge_reason) for r in graded] == [
        (0.2, False, "web"),
        (0.9, True, "right"),
    ]
    assert pending[0].root_cause_score is None  # the raw results stay as they were


def test_a_grade_for_no_run_or_a_run_graded_twice_is_refused(tmp_path):
    grade = {"run_id": "db-down/1", "root_cause_pass": True, "root_cause_score": 1.0, "note": ""}
    with pytest.raises(GradeError, match="hung/1"):
        apply_grades(RESULTS, read_grades(grades_file(tmp_path, grade | {"run_id": "hung/1"})))
    with pytest.raises(GradeError, match="db-down/1"):
        apply_grades(RESULTS, read_grades(grades_file(tmp_path, grade, grade)))


def test_a_grade_out_of_range_is_refused(tmp_path):
    bad = {"run_id": "db-down/1", "root_cause_pass": True, "root_cause_score": 1.5, "note": ""}
    with pytest.raises(GradeError, match="line 1"):
        read_grades(grades_file(tmp_path, bad))


MANUAL = INFO.model_copy(update={"judge": "manual"})
PENDING = [
    r.model_copy(update={"root_cause_score": None, "root_cause_pass": False, "judge_reason": None,
                         "judge": "manual", "judge_usd": None})
    for r in RESULTS
]  # fmt: skip


def test_runs_left_for_a_grade_by_hand_say_so():
    text = render(MANUAL, PENDING)
    assert "| 1 | pending |" in text
    assert "2 diagnosed runs wait for a grade by hand." in text
    assert "Judge: none, graded by hand." in text
    assert "GEval" not in text


def test_a_report_graded_by_hand_names_who_graded_and_links_the_grades():
    info = MANUAL.model_copy(
        update={"grader": "Claude (Opus 5.5)", "grades": "data/grades.jsonl",
                "raw": "data/scenarios-20261103-140000.jsonl"}
    )  # fmt: skip
    right = {"root_cause_score": 1.0, "root_cause_pass": True}
    graded = [r.model_copy(update=right) for r in PENDING]
    text = render(info, graded)
    assert "Claude (Opus 5.5), by hand" in text.split("## Setup")[1].split("## db-down")[0]
    assert "Claude (Opus 5.5) graded the root causes by hand" in text.split("## Setup")[0]
    assert "[`grades.jsonl`](data/grades.jsonl)" in text
    assert "| 1 | 1.00 pass |" in text and "pending" not in text


def test_the_run_info_round_trips_next_to_its_raw_file(tmp_path):
    raw = tmp_path / "scenarios-20261103-140000.jsonl"
    assert read_info(raw) is None
    write_info(raw, INFO)
    assert (tmp_path / "scenarios-20261103-140000.info.json").is_file()
    assert read_info(raw) == INFO


def test_the_report_command_renders_a_batch_with_its_grades(tmp_path, capsys):
    data = tmp_path / "docs" / "evals" / "data"
    raw = data / "scenarios-20261103-140000.jsonl"
    for r in PENDING:
        append_jsonl(raw, r)
    write_info(raw, MANUAL)
    grades = grades_file(
        data,
        {"run_id": "db-down/1", "root_cause_pass": True, "root_cause_score": 1.0, "note": "db"},
        {"run_id": "db-down/2", "root_cause_pass": False, "root_cause_score": 0.0, "note": "no"},
    )
    out = tmp_path / "docs" / "evals" / "scenarios-2026-11-03.md"

    code = main([str(raw), "--grades", str(grades), "--grader", "Claude (Opus 5.5)",
                 "--out", str(out)])  # fmt: skip

    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert "[`scenarios-20261103-140000.jsonl`](data/scenarios-20261103-140000.jsonl)" in text
    assert "[`grades.jsonl`](data/grades.jsonl)" in text
    assert "| 1 | 1.00 pass |" in text and "| 2 | 0.00 fail |" in text
    assert "  - Judge: no" in text
    assert "python -m evals.report" in text  # how to render it again


def test_the_report_command_needs_the_batchs_info_and_a_grader(tmp_path, capsys):
    raw = tmp_path / "scenarios-20261103-140000.jsonl"
    append_jsonl(raw, PENDING[0])
    assert main([str(raw), "--out", str(tmp_path / "r.md")]) == 2
    assert "scenarios-20261103-140000.info.json" in capsys.readouterr().err
    write_info(raw, MANUAL)
    grades = grades_file(tmp_path)
    assert main([str(raw), "--grades", str(grades), "--out", str(tmp_path / "r.md")]) == 2
    assert "--grader" in capsys.readouterr().err


def test_the_totals_count_structured_output_fallbacks():
    text = render(INFO, [result(1, structured_fallbacks=2), result(2, structured_fallbacks=1)])
    totals = text.split("## Totals")[1].split("## Cost")[0]
    header = next(line for line in totals.splitlines() if line.startswith("| Scenario"))
    column = header.split(" | ").index("Fallbacks")
    rows = [line for line in totals.splitlines() if line.startswith(("| db-down", "| **All**"))]
    assert [row.split(" | ")[column] for row in rows] == ["3", "3"]
    assert "3 structured-output fallbacks" in text.split("## What it shows")[1]


def test_the_setup_says_whether_the_shop_was_reset_before_each_run():
    reset = INFO.model_copy(update={"reset_shop": True})
    assert "recreated before every run" in render(reset, RESULTS)
    assert "recreated before every run" not in render(INFO, RESULTS)  # older batches were not


def test_the_setup_says_whether_knowledge_search_was_offered():
    offered = INFO.model_copy(update={"knowledge": True})
    assert "Knowledge search was offered" in render(offered, RESULTS)
    assert "Knowledge search was offered" not in render(INFO, RESULTS)


def test_a_grade_for_a_run_a_judge_model_scored_is_refused(tmp_path):
    """Grading by hand is for a `manual` batch: a judge's score must not be overwritten."""
    grade = {"run_id": "db-down/1", "root_cause_pass": True, "root_cause_score": 1.0, "note": ""}
    with pytest.raises(GradeError, match="db-down/1.*openai/gpt-6-luna"):
        apply_grades(RESULTS, read_grades(grades_file(tmp_path, grade)))


@pytest.mark.parametrize(("passed", "score"), [(True, 0.6), (False, 0.7)])
def test_a_grade_whose_pass_disagrees_with_its_score_is_refused(tmp_path, passed, score):
    bad = {"run_id": "db-down/1", "root_cause_pass": passed, "root_cause_score": score}
    with pytest.raises(GradeError, match=r"(?s)line 1.*a pass is a score of 0\.7 or more"):
        read_grades(grades_file(tmp_path, bad))


def test_a_grade_by_hand_clears_the_judges_cost(tmp_path):
    spent = [r.model_copy(update={"judge_usd": 0.003}) for r in PENDING]
    grade = {"run_id": "db-down/1", "root_cause_pass": True, "root_cause_score": 1.0}
    (graded, _) = apply_grades(spent, read_grades(grades_file(tmp_path, grade)))
    assert graded.judge_usd is None


def test_out_of_context_advice_names_only_what_can_be_done():
    cut = result(3, diagnosis=None, proposal=None, root_cause_score=None,
                 root_cause_pass=False, failure="context_length", error="length")  # fmt: skip
    text = render(INFO, [*RESULTS, cut])
    assert "Give the model a larger context (backend/evals/README.md)." in text
    assert "cap its output" not in text


def test_an_mcp_call_shows_its_tool_and_the_arguments_the_scenario_expects():
    expected = SCENARIO.expected.model_copy(
        update={
            "action": SCENARIO.expected.action.model_validate(
                {
                    "kind": "act",
                    "steps": [
                        {
                            "action": "mcp.call",
                            "target": "shop-proxy",
                            "tool": "caddy_reload",
                            "arguments": {"container": "shop-proxy"},
                        }
                    ],
                }
            )
        }
    )
    scenario = SCENARIO.model_copy(update={"expected": expected})
    params = {"tool": "caddy_reload", "arguments": '{"container": "shop-proxy"}'}
    step = PlanStep(action="mcp.call", host_id="demo", target="shop-proxy", params=params)
    planned = result(1, proposal=Proposal(kind="act", steps=[step], reason="reload"))

    text = render(INFO.model_copy(update={"scenarios": [scenario]}), [planned])

    assert '`mcp.call shop-proxy (caddy_reload) with {"container": "shop-proxy"}`' in text
    assert "mcp.call shop-proxy (caddy_reload)" in text.split("## db-down")[1]
