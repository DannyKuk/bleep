"""A look on request on a host in memory: does Bleep read the service itself before it says
"healthy"?

    cd backend && uv run --group evals python -m evals.look [--only id] [--repeat 3]
                                                            [--render raw.jsonl ...]

One YAML file per case in `evals/look/`: a host in memory (`evals/scripted_host.py`) with the
service's containers and, beside them, another service's (a proxy in front of it), the service's
URLs and what each answers, and whether the right answer is that the service is healthy. Each run
opens the incident with one `look_requested` signal, as a person's tap does, and runs Bleep's own
graph on a throwaway database with the model under test, through Propose; nothing is approved or
executed, and no Docker, demo stack or Bleep backend is involved.

Scored in code, no judge: a run passes when the model was given every one of the service's own
containers' logs (by Gather or a `container_logs` call of its own) and every URL check of the
service, when no plan step is one the case forbids, and when the diagnosis says what the case
expects: for a healthy service category `unknown`, not unsure, one of `healthy_words`, none of
`wrong_words` and the plan no action; for a failing one a category other than `unknown`, one of
`failure_words` and none of `wrong_words`. The case's own expected phrases are taken out of the
root cause before `wrong_words` are looked for, so "no fault" does not count as "fault". A run
that ends early (a timeout, an error, no diagnosis) fails. Reads of containers outside the
service are counted, not failed. See docs/design/evals.md.
"""

import argparse
import asyncio
import logging
import secrets
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Self

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from bleep.graph.build import build_graph, open_checkpointer
from bleep.graph.deps import GraphDeps, InMemoryApprovals, ModelFactory, StaticSettings
from bleep.graph.deps import model_factory as default_models
from bleep.guard.log import GuardLog
from bleep.hosts.config import HostConfig, ServiceOverrides, UrlCheckConfig
from bleep.hosts.registry import Registry
from bleep.incidents.action_log import ActionLog
from bleep.incidents.bus import EventBus
from bleep.incidents.repository import IncidentRepo, utcnow
from bleep.incidents.runner import IncidentRunner
from bleep.llm.usage import CostLedger
from bleep.schemas.graph import Action, Diagnosis, Proposal
from bleep.schemas.incidents import LOOK_REQUESTED, Signal, Subject
from bleep.store.db import Database
from bleep.tools.default import default_registry
from bleep.tools.docker_act import docker_act_tools
from evals.injection import ToolCall
from evals.judge import model_under_test as tested_model
from evals.loopback import UrlAnswer
from evals.run import REPO, EvalError, EvalSettings, _commit, out_of_context
from evals.scenarios import without_words, word_hits
from evals.scripted_host import ScriptedContainer, ScriptedEngine, resolve

log = logging.getLogger("evals.look")

CASES_DIR = Path(__file__).parent / "look"
RAW_DIR = REPO / "data" / "evals"
HOST_ID = "lab"
CONTAINER_READS = frozenset({"inspect_container", "container_logs", "container_stats"})


class LookError(ValueError):
    """A case file is missing, malformed, or not the one asked for."""


class LookCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    service: str  # the Compose project; the look is at its service
    healthy: bool  # the right answer
    urls: list[UrlCheckConfig] = Field(default_factory=list)  # the service's, as in a hosts file
    url_answers: dict[str, UrlAnswer] = Field(default_factory=dict)  # one not listed answers 503
    containers: list[ScriptedContainer] = Field(min_length=2)
    healthy_words: list[str] = Field(default_factory=list)  # one of them, for a healthy service
    failure_words: list[str] = Field(default_factory=list)  # one of them, for a failing one
    wrong_words: list[str] = Field(default_factory=list)  # none of them, in either kind
    must_not: list[Action] = Field(default_factory=list)
    timeout_s: int = Field(400, gt=0)

    @model_validator(mode="after")
    def _fits(self) -> Self:
        if not self.own():
            raise ValueError("the service has no container of its own")
        if len(self.own()) == len(self.containers):
            raise ValueError("a look case has a container outside the service, such as a proxy")
        if self.healthy and not self.healthy_words:
            raise ValueError("a healthy case names its healthy_words")
        if not self.healthy and not self.failure_words:
            raise ValueError("a failing case names its failure_words")
        return self

    def expected_words(self) -> list[str]:
        return self.healthy_words if self.healthy else self.failure_words

    def own(self) -> list[str]:
        return [c.name for c in self.containers if c.project in (None, self.service)]

    @property
    def subject(self) -> Subject:
        return Subject(
            kind="service", host_id=HOST_ID, service_id=f"{HOST_ID}/compose:{self.service}"
        )

    def host(self) -> HostConfig:
        return HostConfig(
            id=HOST_ID,
            name="Scripted host",
            kind="socket",
            endpoint="tcp://scripted:2375",
            services={self.service: ServiceOverrides(urls=self.urls)},
        )


def load_case(path: Path) -> LookCase:
    try:
        case = LookCase.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise LookError(f"case {path}: {error}") from error
    if case.id != path.stem:
        raise LookError(f"case {path}: its id {case.id!r} must be its file name")
    return case


def load_cases(directory: Path = CASES_DIR, only: str | None = None) -> list[LookCase]:
    """Every case in `directory`, or those named in `only` (comma-separated)."""
    paths = sorted(directory.glob("*.yaml"))
    if only is not None:
        wanted = [name.strip() for name in only.split(",") if name.strip()]
        known = {p.stem for p in paths}
        missing = [name for name in wanted if name not in known]
        if missing:
            raise LookError(
                f"no case {', '.join(missing)} in {directory}; known: {', '.join(sorted(known))}"
            )
        paths = [p for p in paths if p.stem in wanted]
    return [load_case(p) for p in paths]


class LookResult(BaseModel):
    """One run of one case. `gathered_logs` and `checked_urls` are what Gather handed the model;
    `tool_calls` the model's own. The scores are filled in by `score`; a run that ended early
    (`failure`, or no diagnosis) has failed."""

    run_id: str
    case: str
    run: int
    model: str
    incident_id: str | None = None
    gathered_logs: list[str] = Field(default_factory=list)
    checked_urls: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    diagnosis: Diagnosis | None = None
    proposal: Proposal | None = None
    tokens: int = 0
    usd: float | None = None
    failure: str | None = None
    error: str | None = None
    unread_logs: list[str] = Field(default_factory=list)
    unchecked_urls: list[str] = Field(default_factory=list)
    outside_reads: list[str] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)
    said_right: bool | None = None
    passed: bool = False


def _logs_read(case: LookCase, result: LookResult) -> set[str]:
    read = set(result.gathered_logs)
    for call in result.tool_calls:
        ref = call.args.get("container")
        if call.name == "container_logs" and isinstance(ref, str):
            read.add(resolve(case.containers, ref) or ref)
    return read


def _outside(case: LookCase, calls: Sequence[ToolCall]) -> list[str]:
    own = set(case.own())
    found = []
    for call in calls:
        ref = call.args.get("container")
        name = resolve(case.containers, ref) if isinstance(ref, str) else None
        if call.name in CONTAINER_READS and name is not None and name not in own:
            found.append(f"{call.name} {name}")
    return found


def _said_right(case: LookCase, result: LookResult) -> bool:
    if result.diagnosis is None:
        return False
    cause, category = result.diagnosis.root_cause, result.diagnosis.category
    expected = word_hits(cause, case.expected_words())
    wrong = word_hits(without_words(cause, case.expected_words()), case.wrong_words)
    if case.healthy:
        no_action = result.proposal is not None and result.proposal.kind == "no_action"
        sure = not result.diagnosis.unsure
        return no_action and sure and category == "unknown" and bool(expected) and not wrong
    return category != "unknown" and bool(expected) and not wrong


def score(case: LookCase, result: LookResult) -> LookResult:
    """Fill in the checks and the verdict of a finished run."""
    read = _logs_read(case, result)
    result.unread_logs = [name for name in case.own() if name not in read]
    result.unchecked_urls = [u.url for u in case.urls if u.url not in result.checked_urls]
    result.outside_reads = _outside(case, result.tool_calls)
    steps = result.proposal.steps if result.proposal and result.proposal.kind == "act" else []
    result.forbidden = [f"{s.action} {s.target}" for s in steps if s.action in case.must_not]
    result.said_right = _said_right(case, result)
    ended_early = result.failure is not None or result.diagnosis is None
    read_all = not result.unread_logs and not result.unchecked_urls
    result.passed = not ended_early and read_all and not result.forbidden and result.said_right
    return result


def url_answers(case: LookCase) -> httpx.MockTransport:
    def answer(request: httpx.Request) -> httpx.Response:
        found = case.url_answers.get(str(request.url))
        status, body = (503, "") if found is None else (found.status, found.body)
        return httpx.Response(status, stream=httpx.ByteStream(body.encode()))

    return httpx.MockTransport(answer)


async def run_case(
    case: LookCase, settings: EvalSettings, *, run: int = 1, models: ModelFactory | None = None
) -> LookResult:
    """One run of `case`: Gather, Investigate, Diagnose and Propose by the model under test; the
    run stops before Approval or the Report. A run that times out or fails keeps what it found."""
    result = LookResult(
        run_id=f"{case.id}/{run}", case=case.id, run=run, model=tested_model(settings.models)
    )
    engine = ScriptedEngine(HOST_ID, case.service, case.containers)

    async def connect(_host: HostConfig) -> tuple[None, ScriptedEngine]:
        return None, engine

    with tempfile.TemporaryDirectory(prefix="bleep-look-") as d:
        db = Database(Path(d) / "bleep.db")
        await db.migrate()
        registry = Registry([case.host()], connect)
        await registry.start()
        secret = secrets.token_hex(16)
        guard_log = GuardLog(db)
        try:
            async with (
                open_checkpointer(Path(d) / "checkpoints.sqlite") as saver,
                httpx.AsyncClient(transport=url_answers(case)) as http,
            ):
                deps = GraphDeps(
                    registry=registry,
                    tools=default_registry(),
                    act_tools=docker_act_tools(secret, guard_log),
                    models=models or default_models(StaticSettings(model_settings=settings.models)),
                    bus=EventBus(db),
                    ledger=CostLedger(db),
                    incidents=IncidentRepo(db),
                    action_log=ActionLog(db),
                    approvals=InMemoryApprovals(secret),
                    guard_log=guard_log,
                    settings_store=StaticSettings(model_settings=settings.models),
                    http=http,
                )
                graph = build_graph(deps, saver)
                try:
                    async with asyncio.timeout(case.timeout_s):
                        await _look(case, graph, deps, result)
                except TimeoutError:
                    result.failure, result.error = "timeout", f"timed out after {case.timeout_s} s"
                except Exception as error:  # the run's failure is a result, not the harness's
                    log.exception("case %s run %d failed", case.id, run)
                    result.failure = "context_length" if out_of_context(error) else "error"
                    result.error = f"{type(error).__name__}: {error}"
                if result.incident_id is not None:
                    await _collect(graph, deps, result)
                await guard_log.flush()
        finally:
            await registry.close()
    return score(case, result)


async def _look(case: LookCase, graph, deps: GraphDeps, result: LookResult) -> None:
    signal = Signal(
        subject=case.subject, kind=LOOK_REQUESTED, at=utcnow(), detail={"via": "telegram"}
    )
    incident, _ = await deps.incidents.open_or_attach(case.subject, [signal])
    result.incident_id = incident.id
    initial = {
        "incident_id": incident.id,
        "subject": incident.subject,
        "signals": await deps.incidents.signals(incident.id),
        "investigate_rounds": 0,
        "tool_calls_used": 0,
        "instructions": [],
        "verify_loops": 0,
    }
    config = IncidentRunner.config(incident.id)
    await graph.ainvoke(initial, config, interrupt_before=["approval", "report"])


async def _collect(graph, deps: GraphDeps, result: LookResult) -> None:
    incident_id = result.incident_id or ""
    state = (await graph.aget_state(IncidentRunner.config(incident_id))).values
    context = state.get("context")
    if context is not None:
        result.gathered_logs = list(context.logs)
        result.checked_urls = [u.url for u in context.urls]
    result.diagnosis = state.get("diagnosis")
    result.proposal = state.get("proposal")
    result.tool_calls = [
        ToolCall(name=str(e.payload.get("name")), args=e.payload.get("args") or {})
        for e in await deps.bus.history(incident_id)
        if e.kind == "tool_call"
    ]
    total = await deps.ledger.incident_total(incident_id)
    result.tokens, result.usd = total.tokens, total.usd


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _plan(proposal: Proposal | None) -> str:
    if proposal is None:
        return "none"
    if proposal.kind == "no_action":
        return "no action"
    return ", ".join(f"{s.action} {s.target}" for s in proposal.steps)


def line(result: LookResult) -> str:
    """One line per finished run, for the console."""
    head = f"{result.case} run {result.run}: "
    if result.failure is not None:
        return head + f"FAILED, ended early ({result.failure}): {result.error}"
    if result.diagnosis is None:
        return head + "FAILED, no diagnosis"
    verdict = "passed" if result.passed else "FAILED"
    found = [f"logs of {name} not read" for name in result.unread_logs]
    found += [f"{url} not checked" for url in result.unchecked_urls]
    found += result.forbidden
    if result.said_right is False:
        found.append("root cause says the wrong thing")
    return head + f"{verdict}; plan {_plan(result.proposal)}" + "".join(f"; {f}" for f in found)


def render(cases: Sequence[LookCase], results: Sequence[LookResult]) -> str:
    """The tables for docs/evals/: one row per case, then one per run."""
    lines = [
        "| Case | Right answer | Passed | Own logs read | URLs checked | Said it right "
        "| Forbidden step | Read outside the service | Ended early |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for case in cases:
        rs = [r for r in results if r.case == case.id]
        if not rs:
            continue

        def of(flag, rs=rs) -> str:
            return f"{sum(bool(flag(r)) for r in rs)} of {len(rs)}"

        lines.append(
            f"| `{case.id}` | {'healthy' if case.healthy else 'failing'} "
            f"| {of(lambda r: r.passed)} | {of(lambda r: not r.unread_logs)} "
            f"| {of(lambda r: not r.unchecked_urls)} | {of(lambda r: r.said_right)} "
            f"| {of(lambda r: r.forbidden)} | {of(lambda r: r.outside_reads)} "
            f"| {sum(r.failure is not None or r.diagnosis is None for r in rs)} |"
        )
    lines += [
        "",
        "| Run | Tool calls | Root cause | Category | Plan | Tokens |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        calls = ", ".join(f"{c.name} {c.args.get('container', '')}".strip() for c in r.tool_calls)
        cause = r.diagnosis.root_cause if r.diagnosis else (r.error or "none")
        category = r.diagnosis.category if r.diagnosis else "none"
        lines.append(
            f"| `{r.run_id}` | {_cell(calls or 'none')} | {_cell(cause)} | {category} "
            f"| {_cell(_plan(r.proposal))} | {r.tokens:,} |"
        )
    usd = [r.usd for r in results if r.usd is not None]
    lines += [
        "",
        f"- {len(results)} runs, {sum(r.tokens for r in results):,} tokens of the model under "
        f"test (${sum(usd):.4f}).",
    ]
    return "\n".join(lines) + "\n"


def rescore(cases: Sequence[LookCase], results: Sequence[LookResult]) -> list[LookResult]:
    """Stored runs scored again with the case files as they are now."""
    by_id = {c.id: c for c in cases}
    return [score(by_id[r.case], r.model_copy(deep=True)) for r in results if r.case in by_id]


def append_jsonl(path: Path, result: LookResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as out:
        out.write(result.model_dump_json() + "\n")


def read_jsonl(path: Path) -> list[LookResult]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [LookResult.model_validate_json(text) for text in lines if text.strip()]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.look", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--only", help="run these cases only: ids, comma-separated")
    parser.add_argument("--repeat", type=int, default=3, help="runs per case (default 3)")
    parser.add_argument(
        "--render", nargs="+", type=Path, metavar="JSONL",
        help="print the tables for these result files instead of running",
    )  # fmt: skip
    return parser


async def _run(args: argparse.Namespace, settings: EvalSettings, cases: list[LookCase]) -> int:
    started = utcnow()
    raw = RAW_DIR / f"look-{started:%Y%m%d-%H%M%S}.jsonl"
    results = []
    for case in cases:
        for n in range(1, args.repeat + 1):
            log.info("case %s, run %d", case.id, n)
            result = await run_case(case, settings, run=n)
            append_jsonl(raw, result)
            results.append(result)
            log.info("%s", line(result))
    tables = raw.with_suffix(".md")
    tables.write_text(
        f"<!-- {tested_model(settings.models)}, scored in code, commit {_commit() or 'unknown'}, "
        f"{started:%Y-%m-%d %H:%M} to {utcnow():%H:%M} UTC -->\n\n" + render(cases, results),
        encoding="utf-8",
    )
    print(f"tables: {tables.relative_to(REPO)}\nraw data: {raw.relative_to(REPO)}")
    return 0


def main(argv: Sequence[str] | None = None, settings: EvalSettings | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        cases = load_cases(only=args.only)
        if args.render:
            print(render(cases, rescore(cases, [r for p in args.render for r in read_jsonl(p)])))
            return 0
        if args.repeat < 1:
            raise EvalError("--repeat must be 1 or more")
        return asyncio.run(_run(args, settings or EvalSettings(), cases))
    except (EvalError, LookError) as error:
        print(f"evals: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
