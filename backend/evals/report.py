"""What a run leaves behind: one JSON line per scenario run as it finishes (`data/evals/`), and
the dated report (`docs/evals/scenarios-YYYY-MM-DD.md`) with the setup, a table per scenario,
the totals, the cost and what the numbers show."""

from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel

from evals.judge import PASS_SCORE
from evals.scenarios import Scenario, ScenarioResult


class RunInfo(BaseModel):
    """How the run was set up, for the report's Setup section. `raw` is the JSONL's path
    relative to the repo."""

    started_at: datetime
    finished_at: datetime
    model: str
    provider: str
    judge: str
    repeat: int
    scenarios: list[Scenario]
    commit: str | None = None
    command: str
    raw: str


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
    return ", ".join(f"{s.action} {s.target}" for s in proposal.steps)


def _expected_plan(scenario: Scenario) -> str:
    action = scenario.expected.action
    if action.kind == "no_action":
        return "no action"
    plan = ", ".join(f"`{a} {t}`" for a, t in action.pairs)
    return plan + (" in this order" if action.ordered and len(action.steps) > 1 else "")


def _score(result: ScenarioResult) -> str:
    if result.root_cause_score is None:
        return "n/a"
    return f"{result.root_cause_score:.2f} {'pass' if result.root_cause_pass else 'fail'}"


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
        "| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tokens "
        "| Peak prompt | Cost | To incident | To diagnosis |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        category = r.diagnosis.category if r.diagnosis else "n/a"
        lines.append(
            f"| {r.run} | {_score(r)} | {category} | {_cell(_plan(r))} | {_yes(r.action_correct)} "
            f"| {_yes(r.unnecessary_action)} | {r.steps} | {r.tokens:,} | {r.peak_prompt_tokens:,} "
            f"| {_usd(r.usd)} | {_seconds(r.seconds_to_incident)} "
            f"| {_seconds(r.seconds_to_diagnosis)} |"
        )
    lines.append("")
    for r in results:
        if r.error:
            lines.append(f"- Run {r.run} ended early: {_cell(r.error)}")
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
    ended = [r for r in results if r.error]
    if ended:
        names = ", ".join(f"{r.id} run {r.run}" for r in ended)
        lines.append(
            f"- {_plural(len(ended), 'run')} ended early ({names}); the errors are listed above."
        )
    unjudged = [r for r in results if r.diagnosis and r.root_cause_score is None]
    if unjudged:
        lines.append(f"- The judge gave no score for {_plural(len(unjudged), 'diagnosed run')}.")
    return lines


def render(info: RunInfo, results: list[ScenarioResult]) -> str:
    minutes = (info.finished_at - info.started_at).total_seconds() / 60
    ids = ", ".join(s.id for s in info.scenarios)
    faults, runs = _plural(len(info.scenarios), "demo fault"), _plural(info.repeat, "run")
    tested_note = " (a local model costs nothing per call)" if info.provider == "local" else ""
    judge_note = " (billed to a plan, not per call)" if info.judge.startswith("claude-code") else ""
    lines = [
        f"# Scenario evals, {info.started_at.date().isoformat()}",
        "",
        f"> `{info.model}` diagnosed and proposed for {faults}, {runs} each; `{info.judge}` "
        f"judged the root causes. The root cause passed in "
        f"{_count([r.root_cause_pass for r in results])} runs and the plan was exact in "
        f"{_count([r.action_correct for r in results])}.",
        "",
        "## Setup",
        "",
        "| | |",
        "|---|---|",
        f"| Model under test | `{info.model}` (provider `{info.provider}`) |",
        f"| Judge | `{info.judge}` through DeepEval GEval; a root cause passes at "
        f"{PASS_SCORE} or more |",
        f"| Scenarios | {ids}, {runs} each |",
        f"| When | {info.started_at:%Y-%m-%d %H:%M} UTC, {minutes:.0f} min |",
        f"| Commit | `{info.commit or 'unknown'}` |",
        f"| Raw data | `{info.raw}` |",
        f"| Reproduce | `{info.command}` (in `backend/`) |",
        "",
        "Each run switches the fault on, lets Bleep's watcher open the incident and runs the graph "
        "to the end of Propose, then switches the fault off. Nothing is executed. Times count from "
        "the fault going on; steps are the model's tool calls; peak prompt is the largest single "
        "prompt of the run.",
        "",
    ]
    for scenario in info.scenarios:
        lines += _scenario_section(scenario, [r for r in results if r.id == scenario.id])
    lines += [
        "## Totals",
        "",
        "| Scenario | Runs | Root cause | Category | Exact plan | Unnecessary | Mean steps "
        "| Mean tokens | Max peak prompt | Cost | Mean to diagnosis |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for scenario in info.scenarios:
        lines.append(_totals_row(scenario.id, [r for r in results if r.id == scenario.id]))
    lines += [
        _totals_row("**All**", results),
        "",
        "## Cost",
        "",
        f"- Model under test: {_usd(_total_usd([r.usd for r in results]))}{tested_note}.",
        f"- Judge: {_usd(_total_usd([r.judge_usd for r in results]))}{judge_note}.",
        "",
        "## What it shows",
        "",
        *_what_it_shows(results),
        "",
    ]
    return "\n".join(lines)
