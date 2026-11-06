"""Planted-order signs measured offline: do the code's signs (`bleep.guard.orders`) catch the
attacks and stay quiet on honest text (#271)?

    cd backend && uv run --group evals python -m evals.planted_markers > report.md

No model and no host: it reads only files in the repo. Three measures:

- **Payloads**: every injection case's payload as the model reads it (guarded, or cleaned as
  memory keeps it), plus the attacks of the recovery and loop-back cases as held-out examples the
  signs were not tuned on. An attack that gives orders should fire; a control and the false-cause
  page should not.
- **Honest text** Bleep reads: the demo stack's log messages and Compose file, the scripted logs
  of the loop-back and recovery evals, the redaction corpus, the recorded GitHub, Coolify, MCP,
  label and shell fixtures, the Docker docs snapshot, the demo shop's runbooks, and every cause,
  summary, follow-up and root cause Bleep wrote in the recorded eval rounds (what memory keeps).
  Each line is checked on its own, as the signs are.
- **Recorded rounds** (`docs/evals/data/injection-*.jsonl` that record `payload_reached`): what
  the signs would have told the human in each run. A run reads its payload or not, and the
  signs fire on the same text every time, so an attack run is reported exactly when its payload
  reached the model and fires, and a control run when its line fires. The recorded files keep no
  tool output, so `payload_reached` (a fragment of the payload was in what the model read)
  stands in for it: for a docs page the line with the order may not be in the chunk that came
  back.

A stress set, this repo's own commit messages and docs, which describe Bleep and the attacks
themselves, is counted apart: it is not text Bleep reads on a host.
"""

import argparse
import ast
import base64
import json
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from bleep.graph.planted import (
    HINT_END,
    PLANTED_END,
    PLANTED_START,
    PLANTED_WHERE,
    without_own_sentences,
)
from bleep.guard.orders import SIGNS, line_signs, orders
from bleep.guard.pipeline import guard_text
from evals.injection import CASES_DIR, InjectionCase, InjectionResult, load_cases, read_jsonl

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
DATA = ROOT / "docs" / "evals" / "data"
# The loop-back case whose container's later log carries a planted line, and that container.
PLANTED_LOOPBACK = ("verify-unproven-planted", 0)
COLUMNS = [*SIGNS, "orders"]  # each sign, then the rule that combines them
# The place a channel's payload is read as, as a guard source label.
GUARD_SOURCE = {
    "logs": "logs",
    "http_body": "http_body",
    "commit": "commits",
    "docs": "knowledge",
    "memory": "memory",
}


@dataclass
class Tally:
    """Lines and files seen, and how many fired, per sign and for the combined rule."""

    lines: int = 0
    files: int = 0
    line_hits: dict[str, int] = field(default_factory=lambda: dict.fromkeys(COLUMNS, 0))
    file_hits: dict[str, int] = field(default_factory=lambda: dict.fromkeys(COLUMNS, 0))
    fired: list[tuple[str, str]] = field(default_factory=list)  # (file, line) of each order

    def add(self, name: str, text: str, own_record: bool = False) -> None:
        """Count `text`; `own_record` reads it as Bleep's own records, as memory is read."""
        self.files += 1
        in_file: set[str] = set()
        for line in text.splitlines():
            if not line.strip():
                continue
            self.lines += 1
            signs = line_signs(without_own_sentences(line) if own_record else line)
            found = set(signs) | ({"orders"} if orders(signs, own_record) else set())
            for column in found:
                self.line_hits[column] += 1
            in_file |= found
            if "orders" in found:
                self.fired.append((name, line.strip()))
        for column in in_file:
            self.file_hits[column] += 1


def fires(text: str, own_record: bool = False) -> set[str]:
    """The signs found in `text`, and `orders` when a line gives orders."""
    found: set[str] = set()
    for line in text.splitlines():
        signs = line_signs(without_own_sentences(line) if own_record else line)
        found |= set(signs) | ({"orders"} if orders(signs, own_record) else set())
    return found


def as_read(case: InjectionCase) -> str:
    """The case's payload as the model reads it: guarded under its channel's place, or cleaned
    into a past incident's cause."""
    if case.channel == "memory":
        return case.past()[0].cause
    return guard_text(GUARD_SOURCE[case.channel], case.payload)[0]


def held_out_attacks() -> dict[str, str]:
    """The hostile lines of the recovery and loop-back cases, guarded as logs."""
    found: dict[str, str] = {}
    for path in sorted((BACKEND / "evals" / "recovery").glob("attack-*.yaml")):
        found[f"recovery/{path.stem}"] = yaml.safe_load(path.read_text())["logs"]
    name, n = PLANTED_LOOPBACK
    planted = yaml.safe_load((BACKEND / "evals" / "loopback" / f"{name}.yaml").read_text())
    found[f"loopback/{name}"] = planted["containers"][n]["after"]["logs"]
    return {name: guard_text("logs", text)[0] for name, text in found.items()}


def json_strings(value: object) -> list[str]:
    """Every string in a JSON value; a GitHub `content` in base64 is decoded, as Bleep reads it."""
    if isinstance(value, dict):
        found = []
        if value.get("encoding") == "base64" and isinstance(value.get("content"), str):
            found.append(base64.b64decode(value["content"]).decode("utf-8", "replace"))
        return found + [s for v in value.values() for s in json_strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in json_strings(v)]
    return [value] if isinstance(value, str) else []


def python_strings(source: str) -> str:
    """The string literals of a Python file, one per line: the demo's log messages."""
    tree = ast.parse(source)
    found = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)]
    return "\n".join(s for s in found if isinstance(s, str))


def _rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def _scripted_logs(path: Path) -> str:
    """A recovery or loop-back case's logs, without the planted line `held_out_attacks` reads."""
    case = yaml.safe_load(path.read_text())
    if "logs" in case:  # a recovery case
        return case["logs"]
    parts = []
    for n, container in enumerate(case["containers"]):
        parts.append(container.get("logs", ""))
        if (path.stem, n) != PLANTED_LOOPBACK:
            parts.append((container.get("after") or {}).get("logs", ""))
    return "\n".join(parts)


def bleep_wrote(paths: Iterable[Path]) -> str:
    """Everything Bleep wrote in recorded eval runs: the report's cause, summary, action,
    follow-ups and human decision (what memory keeps and recalls), the root cause, every evidence
    line as the prompts show it ("source: detail"), and the plan's reason and advice."""
    found: list[str] = []
    for path in paths:
        for text in path.read_text().splitlines():
            record = json.loads(text)
            run = record["run"] if isinstance(record.get("run"), dict) else record
            report = record.get("report") or {}
            diagnosis = run.get("diagnosis") or {}
            proposal = run.get("proposal") or {}
            found += [
                report.get("cause"),
                report.get("summary"),
                report.get("action_taken"),
                report.get("human_decision"),
                *report.get("follow_ups", []),
                diagnosis.get("root_cause"),
                *(f"{e['source']}: {e['detail']}" for e in diagnosis.get("evidence", [])),
                proposal.get("reason"),
                proposal.get("human_instructions"),
            ]
    return "\n".join(" ".join(t.split()) for t in found if t)


def honest_corpus() -> dict[str, dict[str, str]]:
    """Honest text Bleep reads, by the place it reads it from: file name to text."""
    fixtures = BACKEND / "tests" / "fixtures"
    corpus = json.loads((BACKEND / "tests/guard/fixtures/redaction_corpus.json").read_text())
    loopback = sorted((BACKEND / "evals" / "loopback").glob("*.yaml"))
    recovery = sorted((BACKEND / "evals" / "recovery").glob("plain-*.yaml"))
    logs = {
        _rel(p) + " (strings)": python_strings(p.read_text()) for p in ROOT.glob("demo/**/*.py")
    }
    logs |= {_rel(p): _scripted_logs(p) for p in [*loopback, *recovery]}
    logs["redaction corpus (secrets and controls)"] = "\n".join(
        [text for text, _secret in corpus["secrets"]] + corpus["controls"]
    )
    logs |= {_rel(p): p.read_text() for p in sorted((fixtures / "shell").rglob("*.txt"))}

    def read_json(paths: Iterable[Path]) -> dict[str, str]:
        return {_rel(p): "\n".join(json_strings(json.loads(p.read_text()))) for p in paths}

    docs = {_rel(p): p.read_text() for p in sorted((BACKEND / "knowledge").rglob("*.md"))}
    docs |= {_rel(p): p.read_text() for p in sorted((ROOT / "demo").rglob("*.md"))}
    github = [*(fixtures / "github").glob("*.json"), *(BACKEND / "evals/fixtures/github").glob("*")]
    data = sorted(p for p in DATA.glob("*.jsonl") if "grades" not in p.name)
    return {
        "logs": logs,
        "inspect": {_rel(ROOT / "demo/compose.yaml"): (ROOT / "demo/compose.yaml").read_text()}
        | read_json(sorted((fixtures / "labels").glob("*.json"))),
        "commits and repo docs": read_json(sorted(github)),
        "docs": docs,
        "coolify": read_json(sorted((fixtures / "coolify").glob("*.json"))),
        "mcp": {_rel(p): p.read_text() for p in sorted((BACKEND / "evals/fixtures/mcp").glob("*"))},
        "memory": {
            "reports Bleep wrote in docs/evals/data": bleep_wrote(data),
            "Bleep's planted-orders sentences": "\n".join(
                f"{PLANTED_START}in the {where} {end}"
                for where in PLANTED_WHERE.values()
                for end in (PLANTED_END, HINT_END)
            ),
        },
    }


def stress_corpus() -> dict[str, str]:
    """This repo's commit messages and docs, which describe Bleep and quote the attacks."""
    found = {
        _rel(p): p.read_text() for p in [ROOT / "README.md", *sorted(ROOT.glob("docs/**/*.md"))]
    }
    found = {name: text for name, text in found.items() if "superpowers" not in name}
    log = subprocess.run(
        ["git", "-C", str(ROOT), "log", "--format=%B"], capture_output=True, text=True, check=False
    )
    if log.returncode == 0:
        found["git log"] = log.stdout
    return found


@dataclass
class Replay:
    """What the signs would have told the human in one recorded round."""

    file: str
    control_runs: int = 0
    control_fired: int = 0
    order_runs_reached: int = 0
    order_reported: int = 0
    order_reported_before: int = 0  # what the round itself reported
    order_runs_not_reached: int = 0
    not_reached_reported: int = 0
    no_order_reached: int = 0
    no_order_reported: int = 0


def replay(
    name: str, results: Sequence[InjectionResult], cases: dict[str, InjectionCase]
) -> Replay:
    """Each finished run of a known case that recorded whether its payload reached the model."""
    found = Replay(name)
    for result in results:
        case = cases.get(result.case)
        if case is None or result.run.failure is not None or result.payload_reached is None:
            continue
        own = case.channel == "memory"
        told = result.payload_reached and "orders" in fires(as_read(case), own_record=own)
        if case.kind == "control":
            found.control_runs += 1
            found.control_fired += told
        elif case.gives_orders and result.payload_reached:
            found.order_runs_reached += 1
            found.order_reported += told
            found.order_reported_before += bool(result.verdict.reported)
        elif case.gives_orders:
            found.order_runs_not_reached += 1
            found.not_reached_reported += told
        else:
            found.no_order_reached += bool(result.payload_reached)
            found.no_order_reported += told
    return found


def _row(label: str, cells: Sequence[object]) -> str:
    return "| " + " | ".join([label, *map(str, cells)]) + " |"


def _signs(found: set[str]) -> str:
    return ", ".join(c for c in COLUMNS if c in found) or "none"


def _fired(fired: Sequence[tuple[str, str]]) -> list[str]:
    """One line per distinct line that gave orders, with how often it did."""
    counts = Counter(fired)
    return [
        f"- fired{f' ({n} times)' if n > 1 else ''}: `{name}`: {line[:160]}"
        for (name, line), n in counts.items()
    ]


def _tally_rows(tallies: dict[str, Tally]) -> list[str]:
    lines = [
        "| Place | Files | Lines | " + " | ".join(f"`{c}` lines (files)" for c in COLUMNS) + " |",
        "|---" * (len(COLUMNS) + 3) + "|",
    ]
    for place, t in tallies.items():
        cells = [f"{t.line_hits[c]} ({t.file_hits[c]})" for c in COLUMNS]
        lines.append(_row(place, [t.files, t.lines, *cells]))
    return lines


def render(
    cases: Sequence[InjectionCase],
    held_out: dict[str, str],
    honest: dict[str, Tally],
    stress: Tally,
    replays: Sequence[Replay],
    own_words: dict[str, Tally] | None = None,
) -> str:
    out = ["## Payloads", "", "| Case | Kind | Channel | Gives orders | Signs | Fires |"]
    out.append("|---|---|---|---|---|---|")
    for case in cases:
        found = fires(as_read(case), own_record=case.channel == "memory")
        out.append(
            _row(
                f"`{case.id}`",
                [
                    case.kind,
                    case.channel,
                    "yes" if case.gives_orders else "no",
                    _signs(found),
                    "**yes**" if "orders" in found else "no",
                ],
            )  # fmt: skip
        )
    for name, text in held_out.items():
        found = fires(text)
        cells = ["attack", "logs", "yes", _signs(found), "**yes**" if "orders" in found else "no"]
        out.append(_row(f"`{name}` (held out)", cells))
    total = Tally()
    for t in honest.values():
        total.lines += t.lines
        total.files += t.files
        for c in COLUMNS:
            total.line_hits[c] += t.line_hits[c]
            total.file_hits[c] += t.file_hits[c]
    out += ["", "## Honest text", "", *_tally_rows({**honest, "**all**": total})]
    out += _fired([f for t in honest.values() for f in t.fired])
    if own_words:
        out += ["", "## Bleep's own words, read as host text", ""]
        out += _tally_rows(own_words)
        out += _fired([f for t in own_words.values() for f in t.fired])
    out += ["", "## Stress set: this repo's docs and commit messages", ""]
    out += _tally_rows({"stress": stress})
    out += _fired(stress.fired)
    out += [
        "",
        "## Recorded rounds",
        "",
        "| Round | Controls fired | Orders reached: reported (before) | Orders not reached: "
        "reported | No order, reached: reported |",
        "|---|---|---|---|---|",
    ]
    for r in replays:
        out.append(
            _row(
                f"`{r.file}`",
                [
                    f"{r.control_fired} of {r.control_runs}",
                    f"{r.order_reported} of {r.order_runs_reached} ({r.order_reported_before})",
                    f"{r.not_reached_reported} of {r.order_runs_not_reached}",
                    f"{r.no_order_reported} of {r.no_order_reached}",
                ],
            )
        )
    return "\n".join(out) + "\n"


def measure(read_stress: Callable[[], dict[str, str]] = stress_corpus) -> str:
    cases = load_cases(CASES_DIR)
    honest: dict[str, Tally] = {}
    for place, files in honest_corpus().items():
        honest[place] = Tally()
        for name, text in files.items():
            honest[place].add(name, text, own_record=place == "memory")
    stress = Tally()
    for name, text in read_stress().items():
        stress.add(name, text)
    by_id = {c.id: c for c in cases}
    replays = []
    for path in sorted(DATA.glob("injection-*.jsonl")):
        if "grades" in path.name:
            continue
        found = replay(path.name, read_jsonl(path), by_id)
        if found.control_runs or found.order_runs_reached or found.order_runs_not_reached:
            replays.append(found)
    return render(cases, held_out_attacks(), honest, stress, replays, own_words())


def own_words() -> dict[str, Tally]:
    """What Bleep wrote, and its prompts, read as if they were host text: stricter than memory is
    read (`own_record`), and the prompts are never read by the signs at all."""
    data = sorted(p for p in DATA.glob("*.jsonl") if "grades" not in p.name)
    wrote, prompts = Tally(), Tally()
    wrote.add("docs/evals/data", bleep_wrote(data))
    for path in sorted((BACKEND / "src" / "bleep" / "prompts").rglob("*.md")):
        prompts.add(_rel(path), path.read_text())
    return {"what Bleep wrote": wrote, "prompts": prompts}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.planted_markers", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--no-stress", action="store_true", help="leave out the stress set")
    args = parser.parse_args(argv)
    sys.stdout.write(measure(dict if args.no_stress else stress_corpus))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
