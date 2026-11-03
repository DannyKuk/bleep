"""The dated report and the raw JSONL a run writes."""

from datetime import UTC, date, datetime

from bleep.schemas.graph import Diagnosis, PlanStep, Proposal
from evals.report import RunInfo, append_jsonl, read_jsonl, render, report_path
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
