"""What a run leaves behind: one JSON line per scenario run as it finishes (`data/evals/`), the
batch's setup next to it (`<raw>.info.json`), and the dated report
(`docs/evals/scenarios-YYYY-MM-DD.md`) with the setup, a table per scenario, the totals, the cost
and what the numbers show.

Runs judged `manual` leave their root cause pending. A grades file written by hand, one JSON line
per run, is applied to the raw results and the report rendered again:

    cd backend && uv run --group evals python -m evals.report <raw.jsonl> --grades <grades.jsonl> \
        --grader "<who graded>" [--out <report.md>]
"""

import argparse
import json
import os
import shlex
import sys
from collections.abc import Collection, Sequence
from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from evals.judge import MANUAL, PASS_SCORE
from evals.scenarios import ExpectedStep, Scenario, ScenarioResult, step_key

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"


class RunInfo(BaseModel):
    """How the run was set up, for the report's Setup section. `raw` is the JSONL's path as the
    report links it, relative to the report's folder. `report` is the report's path relative to
    the repo, which a later batch of the same suite writes again. `grader` and `grades` are set
    once grades by hand are applied: who graded, and the grades file as the report links it.
    `reset_shop` says the shop's containers were recreated before every run (#104); batches
    from before the reset have it false. `knowledge` says the model was offered
    `knowledge_search` over the Docker docs snapshot (#39); batches from before it have it false."""

    started_at: datetime
    finished_at: datetime
    model: str
    provider: str
    judge: str
    repeat: int
    scenarios: list[Scenario]
    commit: str | None = None
    command: str
    reset_shop: bool = False
    knowledge: bool = False
    raw: str
    report: str | None = None
    grader: str | None = None
    grades: str | None = None


def info_path(raw: Path) -> Path:
    """The batch's setup, next to its raw file: `<raw stem>.info.json`."""
    return raw.with_name(f"{raw.stem}.info.json")


def write_info(raw: Path, info: RunInfo) -> None:
    path = info_path(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(info.model_dump_json(indent=2) + "\n", encoding="utf-8")


def read_info(raw: Path) -> RunInfo | None:
    path = info_path(raw)
    if not path.is_file():
        return None
    return RunInfo.model_validate_json(path.read_text(encoding="utf-8"))


class GradeError(ValueError):
    """A grades file that is malformed or does not fit the runs."""


class Grade(BaseModel):
    """One run's root cause, graded by hand against the scenario's expected root cause."""

    model_config = ConfigDict(extra="forbid")

    run_id: str  # `<scenario>/<run>`, as `ScenarioResult.run_id`
    root_cause_pass: bool
    root_cause_score: float = Field(ge=0, le=1)
    note: str = ""

    @model_validator(mode="after")
    def _pass_matches_score(self) -> Grade:
        """A pass is a score of PASS_SCORE or more, as for the judge, so the two cannot disagree."""
        if self.root_cause_pass != (self.root_cause_score >= PASS_SCORE):
            verdict = "pass" if self.root_cause_pass else "fail"
            raise ValueError(
                f"{self.run_id}: a {verdict} with score {self.root_cause_score}; a pass is a score "
                f"of {PASS_SCORE} or more"
            )
        return self


def read_grades(path: Path) -> list[Grade]:
    grades = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            grades.append(Grade.model_validate_json(line))
        except ValidationError as error:
            raise GradeError(f"{path}, line {number}: {error}") from error
    return grades


def index_grades(grades: Sequence[Grade], run_ids: Collection[str]) -> dict[str, Grade]:
    """`grades` by run id. A grade for a run that is not in `run_ids`, or a run graded twice, is
    refused."""
    by_run: dict[str, Grade] = {}
    for grade in grades:
        if grade.run_id not in run_ids:
            raise GradeError(f"a grade for {grade.run_id}, which is not a run in the results")
        if grade.run_id in by_run:
            raise GradeError(f"{grade.run_id} is graded twice")
        by_run[grade.run_id] = grade
    return by_run


def graded_run(result: ScenarioResult, grade: Grade) -> ScenarioResult:
    """A copy of `result` with the grade's root cause score, pass and note (as the judge's
    reason). No judge model was paid for a grade by hand, so its cost is cleared."""
    update = {
        "root_cause_score": grade.root_cause_score,
        "root_cause_pass": grade.root_cause_pass,
        "judge_reason": grade.note or None,
        "judge_usd": None,
    }
    return result.model_copy(update=update)


def apply_grades(
    results: Sequence[ScenarioResult], grades: Sequence[Grade]
) -> list[ScenarioResult]:
    """The results with each graded run's grade applied. A grade for a run that is not in the
    results, a run graded twice, or a run a judge model scored (only a `manual` batch is graded by
    hand) is refused; a run with no grade stays as it was."""
    by_run = index_grades(grades, {r.run_id for r in results})
    for r in results:
        if r.run_id in by_run and r.judge != MANUAL:
            raise GradeError(
                f"a grade for {r.run_id}, whose root cause the judge {r.judge} scored; only a "
                "batch run with --judge manual is graded by hand"
            )
    return [graded_run(r, by_run[r.run_id]) if r.run_id in by_run else r for r in results]


def report_path(day: date, docs_dir: Path) -> Path:
    """`scenarios-<day>.md`, or `-2`, `-3`, ... after it, so a second run never overwrites one."""
    path = docs_dir / f"scenarios-{day.isoformat()}.md"
    n = 2
    while path.exists():
        path = docs_dir / f"scenarios-{day.isoformat()}-{n}.md"
        n += 1
    return path


def append_jsonl(path: Path, result: ScenarioResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as out:
        out.write(result.model_dump_json() + "\n")


def read_jsonl(path: Path) -> list[ScenarioResult]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ScenarioResult.model_validate_json(line) for line in lines if line.strip()]


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _count(flags: Sequence[bool]) -> str:
    return f"{sum(flags)} of {len(flags)}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _usd(value: float | None) -> str:
    return "n/a" if value is None else f"${value:.4f}"


def _total_usd(values: Sequence[float | None]) -> float | None:
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def _seconds(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f} s"


def _mean(values: Sequence[float | None]) -> float | None:
    known = [v for v in values if v is not None]
    return sum(known) / len(known) if known else None


def _plan(result: ScenarioResult) -> str:
    proposal = result.proposal
    if proposal is None:
        return "none"
    if proposal.kind == "no_action":
        return "no action"
    return ", ".join(_step(*step_key(s)) for s in proposal.steps)


def _tools(result: ScenarioResult) -> str:
    if not result.tool_calls:
        return "n/a"
    return ", ".join(
        f"`{c.name}({c.args})`" if c.args else f"`{c.name}`" for c in result.tool_calls
    )


def _step(action: str, target: str, tool: str | None) -> str:
    return f"{action} {target}" + (f" ({tool})" if tool else "")


def _expected_step(step: ExpectedStep) -> str:
    shown = _step(*step.key)
    if step.arguments is None:
        return shown
    return f"{shown} with {json.dumps(step.arguments, separators=(', ', ': '))}"


def _expected_plan(scenario: Scenario) -> str:
    action = scenario.expected.action
    if action.kind == "no_action":
        return "no action"
    plan = ", ".join(f"`{_expected_step(step)}`" for step in action.steps)
    return plan + (" in this order" if action.ordered and len(action.steps) > 1 else "")


def _link(path: str) -> str:
    return f"[`{Path(path).name}`]({path})"


def _by_hand(info: RunInfo) -> bool:
    return info.judge == MANUAL


def _pending(result: ScenarioResult) -> bool:
    """A diagnosed run left for a grade by hand that has none yet."""
    graded = result.root_cause_score is not None
    return result.judge == MANUAL and result.diagnosis is not None and not graded


def _score(result: ScenarioResult) -> str:
    if _pending(result):
        return "pending"
    if result.root_cause_score is None:
        return "n/a"
    return f"{result.root_cause_score:.2f} {'pass' if result.root_cause_pass else 'fail'}"


def _out_of_context(results: Sequence[ScenarioResult]) -> list[ScenarioResult]:
    return [r for r in results if r.failure == "context_length"]


def _yes(flag: bool) -> str:
    return "yes" if flag else "no"


def _scenario_section(scenario: Scenario, results: list[ScenarioResult]) -> list[str]:
    expected = scenario.expected
    never = ", ".join(f"`{a}`" for a in expected.must_not)
    lines = [
        f"## {scenario.id}",
        "",
        f"- Fault: `{scenario.fault}`",
        f"- Expected root cause: {_cell(expected.root_cause)}",
        f"- Expected category `{expected.category}`, plan {_expected_plan(scenario)}"
        + (f", never {never}" if never else ""),
        "",
        "| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens "
        "| Peak prompt | Cost | To incident | To diagnosis |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        category = r.diagnosis.category if r.diagnosis else "n/a"
        lines.append(
            f"| {r.run} | {_score(r)} | {category} | {_cell(_plan(r))} | {_yes(r.action_correct)} "
            f"| {_yes(r.unnecessary_action)} | {r.steps} "
            f"| {_cell(_tools(r))} | {r.tokens:,} | {r.peak_prompt_tokens:,} "
            f"| {_usd(r.usd)} | {_seconds(r.seconds_to_incident)} "
            f"| {_seconds(r.seconds_to_diagnosis)} |"
        )
    lines.append("")
    for r in results:
        if r.error:
            how = f" ({r.failure})" if r.failure else ""
            lines.append(f"- Run {r.run} failed{how}: {_cell(r.error)}")
        if r.diagnosis:
            lines.append(f"- Run {r.run} diagnosis: {_cell(r.diagnosis.root_cause)}")
        if r.judge_reason:
            lines.append(f"  - Judge: {_cell(r.judge_reason)}")
        if r.forbidden:
            lines.append(f"  - Forbidden steps proposed: {', '.join(r.forbidden)}")
    lines.append("")
    return lines


def _totals_row(name: str, results: list[ScenarioResult]) -> str:
    steps = _mean([r.steps for r in results])
    tokens = _mean([r.tokens for r in results])
    return (
        f"| {name} | {len(results)} | {_count([r.root_cause_pass for r in results])} "
        f"| {_count([r.category_correct for r in results])} "
        f"| {_count([r.action_correct for r in results])} "
        f"| {_count([r.unnecessary_action for r in results])} "
        f"| {len([r for r in results if r.failure])} "
        f"| {len(_out_of_context(results))} "
        f"| {sum(r.structured_fallbacks for r in results)} "
        f"| {steps or 0:.1f} | {tokens or 0:,.0f} "
        f"| {max((r.peak_prompt_tokens for r in results), default=0):,} "
        f"| {_usd(_total_usd([r.usd for r in results]))} "
        f"| {_seconds(_mean([r.seconds_to_diagnosis for r in results]))} |"
    )


def _what_it_shows(results: list[ScenarioResult]) -> list[str]:
    lines = [
        f"- The root cause passed in {_count([r.root_cause_pass for r in results])} runs, and "
        f"the category was right in {_count([r.category_correct for r in results])}.",
        f"- The plan was exactly the expected one in {_count([r.action_correct for r in results])}"
        f" runs; {sum(r.unnecessary_action for r in results)} proposed an unnecessary action.",
    ]
    if results:
        peak = max(results, key=lambda r: r.peak_prompt_tokens)
        lines.append(
            f"- The largest single prompt was {peak.peak_prompt_tokens:,} tokens "
            f"({peak.id}, run {peak.run})."
        )
        fallbacks = sum(r.structured_fallbacks for r in results)
        lines.append(
            f"- {_plural(fallbacks, 'structured-output fallback')} in "
            f"{_plural(sum(r.structured_fallbacks > 0 for r in results), 'run')}: model calls "
            "that gave no structured output through tools, so Bleep asked for JSON instead."
        )
    ended = [r for r in results if r.error]
    if ended:
        names = ", ".join(f"{r.id} run {r.run}" for r in ended)
        lines.append(
            f"- {_plural(len(ended), 'run')} failed ({names}); the errors are listed above."
        )
    cut = _out_of_context(results)
    if cut:
        lines.append(
            f"- {_plural(len(cut), 'run')} failed with `context_length`: a model call reached its "
            "length limit before it answered, its reasoning having filled the context. Give the "
            "model a larger context (backend/evals/README.md)."
        )
    pending = [r for r in results if _pending(r)]
    if pending:
        lines.append(f"- {_plural(len(pending), 'diagnosed run')} wait for a grade by hand.")
    unjudged = [
        r for r in results if r.diagnosis and r.root_cause_score is None and not _pending(r)
    ]
    if unjudged:
        lines.append(f"- The judge gave no score for {_plural(len(unjudged), 'diagnosed run')}.")
    return lines


def _judge_row(info: RunInfo) -> str:
    if not _by_hand(info):
        return (
            f"| Judge | `{info.judge}` through DeepEval GEval; a root cause passes at "
            f"{PASS_SCORE} or more |"
        )
    if info.grader is None:
        return "| Judge | by hand, not graded yet |"
    where = f"; every grade and its note in {_link(info.grades)}" if info.grades else ""
    return f"| Judge | {info.grader}, by hand, against each scenario's expected root cause{where} |"


def _who_judged(info: RunInfo) -> str:
    if not _by_hand(info):
        return f"`{info.judge}` judged the root causes"
    if info.grader is None:
        return "the root causes wait for a grade by hand"
    return f"{info.grader} graded the root causes by hand"


def render(info: RunInfo, results: list[ScenarioResult], regrade: str | None = None) -> str:
    """The report. `regrade` is the command that applied the grades by hand, if any."""
    minutes = (info.finished_at - info.started_at).total_seconds() / 60
    ids = ", ".join(s.id for s in info.scenarios)
    faults, runs = _plural(len(info.scenarios), "demo fault"), _plural(info.repeat, "run")
    tested_note = " (a local model costs nothing per call)" if info.provider == "local" else ""
    judge_note = " (billed to a plan, not per call)" if info.judge.startswith("claude-code") else ""
    judge_cost = (
        "none, graded by hand"
        if _by_hand(info)
        else _usd(_total_usd([r.judge_usd for r in results])) + judge_note
    )
    lines = [
        f"# Scenario evals, {info.started_at.date().isoformat()}",
        "",
        f"> `{info.model}` diagnosed and proposed for {faults}, {runs} each; "
        f"{_who_judged(info)}. The root cause passed in "
        f"{_count([r.root_cause_pass for r in results])} runs and the plan was exact in "
        f"{_count([r.action_correct for r in results])}."
        + (
            f" {_plural(len(_out_of_context(results)), 'run')} ran out of context."
            if _out_of_context(results)
            else ""
        ),
        "",
        "## Setup",
        "",
        "| | |",
        "|---|---|",
        f"| Model under test | `{info.model}` (provider `{info.provider}`) |",
        _judge_row(info),
        f"| Scenarios | {ids}, {runs} each |",
        f"| When | {info.started_at:%Y-%m-%d %H:%M} UTC, {minutes:.0f} min |",
        f"| Commit | `{info.commit or 'unknown'}` |",
        f"| Raw data | {_link(info.raw)} |",
        f"| Reproduce | `{info.command}` (in `backend/`) |",
        *([f"| Graded | `{regrade}` (in `backend/`) |"] if regrade else []),
        "",
        "Each run switches the fault on, lets Bleep's watcher open the incident and runs the graph "
        "to the end of Propose, then switches the fault off. Nothing is executed. Times count from "
        "the fault going on; steps are the model's tool calls, and tools lists them in order with "
        "their arguments cut short (n/a for a run recorded before they were kept); peak prompt is "
        "the largest single "
        "prompt of the run; fallbacks are model calls that gave no structured output through "
        "tools, so Bleep asked for JSON in the reply instead."
        + (
            " The shop's containers were recreated before every run, volumes kept, so no earlier "
            "run's log reached the next."
            if info.reset_shop
            else ""
        )
        + (
            " Knowledge search was offered over the shipped Docker docs snapshot, as in the app."
            if info.knowledge
            else ""
        ),
        "",
    ]
    for scenario in info.scenarios:
        lines += _scenario_section(scenario, [r for r in results if r.id == scenario.id])
    lines += [
        "## Totals",
        "",
        "| Scenario | Runs | Root cause | Category | Exact plan | Unnecessary | Failed "
        "| Out of context | Fallbacks | Mean steps | Mean tokens | Max peak prompt | Cost "
        "| Mean to diagnosis |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for scenario in info.scenarios:
        lines.append(_totals_row(scenario.id, [r for r in results if r.id == scenario.id]))
    lines += [
        _totals_row("**All**", results),
        "",
        "## Cost",
        "",
        f"- Model under test: {_usd(_total_usd([r.usd for r in results]))}{tested_note}.",
        f"- Judge: {judge_cost}.",
        "",
        "## What it shows",
        "",
        *_what_it_shows(results),
        "",
    ]
    return "\n".join(lines)


def _from_backend(path: Path) -> str:
    """`path` as a command run in `backend/` names it."""
    path = path.resolve()
    return os.path.relpath(path, BACKEND) if path.is_relative_to(REPO) else str(path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.report",
        description="Render a batch's report again from its raw file, with grades by hand.",
    )
    parser.add_argument("raw", type=Path, help="the batch's raw JSONL; its .info.json next to it")
    parser.add_argument("--grades", type=Path, help="grades by hand, one JSON line per run")
    parser.add_argument("--grader", help="who graded, as the report names them")
    parser.add_argument(
        "--out", type=Path, help="the report to write; default: the batch's own report"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        info = read_info(args.raw)
        if info is None:
            raise GradeError(f"no batch setup at {info_path(args.raw)}")
        if args.grades and not args.grader:
            raise GradeError("--grades needs --grader, who graded them")
        if args.out:
            out = args.out
        elif info.report:
            out = REPO / info.report
        else:
            raise GradeError("the batch names no report; give --out")
        results = read_jsonl(args.raw)
        here = out.resolve().parent
        update: dict[str, str | None] = {"raw": os.path.relpath(args.raw.resolve(), here)}
        regrade = None
        if args.grades:
            results = apply_grades(results, read_grades(args.grades))
            update |= {
                "grader": args.grader,
                "grades": os.path.relpath(args.grades.resolve(), here),
            }
            regrade = shlex.join(
                ["uv", "run", "--group", "evals", "python", "-m", "evals.report"]
                + [_from_backend(args.raw), "--grades", _from_backend(args.grades)]
                + ["--grader", args.grader, "--out", _from_backend(out)]
            )
        info = info.model_copy(update=update)
    except (OSError, ValidationError, GradeError) as error:
        print(f"evals.report: {error}", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(info, results, regrade), encoding="utf-8")
    print(f"report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
