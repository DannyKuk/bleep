"""Run fault scenarios on the demo stack and score what Bleep diagnoses and proposes.

    cd backend && uv run --group evals python -m evals.run [--model X] [--only id] [--repeat N]

Each run builds Bleep's watcher and incident graph in this process, on a throwaway SQLite
database, with Telegram off, no memory and no act tools. It switches the scenario's fault on with
`demo/fault.sh`, waits for the watcher to open the scenario's incident, runs the graph to the end
of Propose and stops there: evals never execute anything. Then it switches the fault off, has the
judge score the root cause, and checks the category and the plan exactly. Results go to
`data/evals/` as JSONL while the run goes, and to a dated report in `docs/evals/` at the end.
See README.md next to this file and docs/design/evals.md.
"""

import argparse
import asyncio
import logging
import os
import subprocess
import sys
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol, Self

import httpx
from langgraph.graph.state import CompiledStateGraph
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from bleep.graph.build import build_graph, open_checkpointer
from bleep.graph.deps import GraphDeps, ModelFactory, StaticSettings, model_factory
from bleep.guard.log import GuardLog
from bleep.hosts.config import HostConfig, ServiceOverrides, load_hosts_file
from bleep.hosts.discovery import is_self
from bleep.hosts.engine import ContainerNotFound
from bleep.hosts.registry import Connector, Registry, connect_host
from bleep.incidents.action_log import ActionLog
from bleep.incidents.bus import EventBus
from bleep.incidents.repository import IncidentRepo, utcnow
from bleep.incidents.runner import IncidentRunner
from bleep.llm.usage import CostLedger
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.incidents import Incident, Subject
from bleep.schemas.settings import ModelSettings, Thresholds
from bleep.store.db import Database
from bleep.tools.default import default_registry
from bleep.watcher.detector import Detector
from bleep.watcher.http_checks import UrlCheckStore
from bleep.watcher.manager import WatcherManager
from evals.judge import DEFAULT_JUDGE, Judge, JudgeError, build_judge, check_distinct
from evals.judge import model_under_test as tested_model
from evals.report import RunInfo, append_jsonl, render, report_path
from evals.scenarios import (
    Scenario,
    ScenarioError,
    ScenarioResult,
    load_scenarios,
    score_action,
    score_category,
)

log = logging.getLogger("evals")

REPO = Path(__file__).resolve().parents[2]
ENV_FILE = REPO / ".env"
REPORTS_DIR = REPO / "docs" / "evals"
RAW_DIR = REPO / "data" / "evals"
# The graph stops before these nodes: Approval is where a plan would be put to a human and then
# executed, and Report would write to memory. Propose is the last node an eval runs.
STOP_BEFORE = ["approval", "report"]
FAULT_TIMEOUT_S = 300
COMPOSE_SERVICE = "com.docker.compose.service"
DOCKER_DESKTOP_SOCKET = Path.home() / ".docker" / "run" / "docker.sock"


# (container name, container port) -> the port it is published on, on this machine
Published = dict[tuple[str, int], int]


def _from_this_machine(url: str, published: Published | None = None) -> str:
    """A URL as this machine reaches it. `host.docker.internal` is a container's name for this
    machine; a container's own name and port become `localhost` and the port it is published on."""
    parsed = httpx.URL(url)
    if parsed.host == "host.docker.internal":
        return str(parsed.copy_with(host="localhost"))
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if published and (parsed.host, port) in published:
        return str(parsed.copy_with(host="localhost", port=published[(parsed.host, port)]))
    return url


class EvalError(Exception):
    """The harness cannot run as configured."""


class FaultError(EvalError):
    """`demo/fault.sh` failed to switch a fault."""


class EvalSettings(BaseSettings):
    """The harness's own settings (`EVAL_*`), and Bleep's model settings for the model under
    test (`LLM_PROVIDER`, `LLM_MODEL`, `LOCAL_*`). Both read the environment, then the repo's
    `.env`."""

    model_config = SettingsConfigDict(
        env_prefix="EVAL_", env_file=ENV_FILE, env_ignore_empty=True, extra="ignore"
    )

    judge_model: str = DEFAULT_JUDGE
    # The demo host's Docker engine as seen from this machine; unset, DOCKER_HOST, then Docker
    # Desktop's socket, then /var/run/docker.sock.
    docker_endpoint: str | None = None
    hosts_file: Path = REPO / "config" / "hosts.demo.yaml"
    host_id: str = "demo"
    fault_script: Path = REPO / "demo" / "fault.sh"
    settle_s: float = 30  # wait between runs, so the stack is back to normal before the next
    watch_interval_s: float = 15  # Bleep's default, so the time to an incident is the real one
    models: ModelSettings = Field(default_factory=lambda: ModelSettings(_env_file=ENV_FILE))

    @model_validator(mode="after")
    def _model_server_from_this_machine(self) -> Self:
        """`.env` names the local model server as a container reaches it; the harness runs on
        the machine itself."""
        url = _from_this_machine(self.models.local_base_url)
        if url != self.models.local_base_url:
            self.models = self.models.model_copy(update={"local_base_url": url})
        return self

    def with_model(self, model: str) -> EvalSettings:
        """These settings with `model` under test, for the provider they name."""
        field = "local_model" if self.models.provider == "local" else "model"
        return self.model_copy(update={"models": self.models.model_copy(update={field: model})})


class Faults(Protocol):
    async def switch(self, fault: str, state: Literal["on", "off"]) -> None: ...


class DemoFaults:
    """`demo/fault.sh <fault> on|off`, run from the repo root."""

    def __init__(self, script: Path, timeout_s: float = FAULT_TIMEOUT_S) -> None:
        self.script = script
        self.timeout_s = timeout_s

    async def switch(self, fault: str, state: Literal["on", "off"]) -> None:
        what = f"{self.script.name} {fault} {state}"
        process = await asyncio.create_subprocess_exec(
            str(self.script),
            fault,
            state,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=REPO,
        )
        try:
            _, err = await asyncio.wait_for(process.communicate(), self.timeout_s)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise FaultError(f"{what}: no end within {self.timeout_s} s") from None
        if process.returncode != 0:
            tail = err.decode(errors="replace").strip()[-500:]
            raise FaultError(f"{what} exited {process.returncode}: {tail}")


def docker_endpoint(settings: EvalSettings) -> str:
    if settings.docker_endpoint:
        return settings.docker_endpoint
    if os.environ.get("DOCKER_HOST"):
        return os.environ["DOCKER_HOST"]
    if DOCKER_DESKTOP_SOCKET.exists():
        return f"unix://{DOCKER_DESKTOP_SOCKET}"
    return "unix:///var/run/docker.sock"


def _demo_host(settings: EvalSettings) -> HostConfig:
    hosts = load_hosts_file(settings.hosts_file).hosts
    host = next((h for h in hosts if h.id == settings.host_id), None)
    if host is None:
        known = ", ".join(h.id for h in hosts)
        raise EvalError(f"no host {settings.host_id!r} in {settings.hosts_file}; known: {known}")
    if host.kind == "socket":
        host = host.model_copy(update={"endpoint": docker_endpoint(settings)})
    return host


async def _published(host: HostConfig, connector: Connector, names: set[str]) -> Published:
    """The published ports of the containers named in `names`; a name that is no container on
    the host is left out."""
    try:
        session, engine = await connector(host)
    except Exception as error:
        where = host.endpoint or host.address
        raise EvalError(f"cannot reach the demo host's Docker engine ({where}): {error}") from error
    published: Published = {}
    try:
        for name in sorted(names):
            try:
                detail = await engine.inspect(name)
            except ContainerNotFound:
                continue
            for binding in detail.ports:
                number, _, _protocol = binding.container_port.partition("/")
                if binding.host_port and number.isdigit():
                    published[(name, int(number))] = int(binding.host_port)
    finally:
        await engine.close()
        if session is not None:
            await session.close()
    return published


async def eval_host(settings: EvalSettings, connector: Connector = connect_host) -> HostConfig:
    """The demo host from the hosts file, reached from this machine. A socket host talks to the
    local engine instead of the proxy inside Compose; a URL for `host.docker.internal`, or for a
    container by name, goes to the port it is published on here. Rules, protected containers and
    the rest stay as the file says. A URL that stays out of reach is logged: its checks fail."""
    host = _demo_host(settings)
    urls = [u.url for service in host.services.values() for u in service.urls]
    names = {httpx.URL(url).host for url in urls} - {"localhost", "127.0.0.1"}
    published = await _published(host, connector, names) if names else {}
    services: dict[str, ServiceOverrides] = {}
    for name, service in host.services.items():
        moved = [
            u.model_copy(update={"url": _from_this_machine(u.url, published)}) for u in service.urls
        ]
        for before, after in zip(service.urls, moved, strict=True):
            host_name = httpx.URL(after.url).host
            if after.url == before.url and host_name not in ("localhost", "127.0.0.1"):
                log.warning("%s may not answer from this machine; its checks may fail", after.url)
        services[name] = service.model_copy(update={"urls": moved})
    return host.model_copy(update={"services": services})


def live_backend(containers: Sequence[ContainerSummary]) -> bool:
    """Whether Bleep's own backend is running on this engine: it would see the fault too, open
    its own incident and page the human."""
    return any(
        is_self(c.labels) and c.labels.get(COMPOSE_SERVICE) == "backend" and c.state == "running"
        for c in containers
    )


class IncidentCatcher(IncidentRunner):
    """Takes the runner's place in the watcher: it starts no run, it hands each newly opened
    incident to the harness, which runs only the scenario's own one, and only to Propose."""

    def __init__(self, graph: CompiledStateGraph, repo: IncidentRepo, bus: EventBus) -> None:
        super().__init__(graph, repo, bus)
        self.opened: asyncio.Queue[Incident] = asyncio.Queue()

    async def start(self, incident: Incident) -> asyncio.Task[None]:
        self.opened.put_nowait(incident)
        return asyncio.create_task(asyncio.sleep(0))

    async def recover_on_boot(self) -> list[str]:
        return []  # a fresh database has nothing to recover

    async def wait_for(self, subject: Subject) -> Incident:
        while True:
            incident = await self.opened.get()
            if incident.subject.key == subject.key:
                return incident
            log.info("ignoring incident %s on %s", incident.id, incident.subject.key)


def _graph_deps(
    db: Database, registry: Registry, settings: EvalSettings, models: ModelFactory | None
) -> GraphDeps:
    """Bleep's graph parts, minus everything that reaches past Propose or out of the process:
    no act tools, no memory (each run starts blank), no Telegram."""
    store = StaticSettings(model_settings=settings.models)
    return GraphDeps(
        registry=registry,
        tools=default_registry(),
        act_tools={},
        models=models or model_factory(store),
        bus=EventBus(db),
        ledger=CostLedger(db),
        incidents=IncidentRepo(db),
        action_log=ActionLog(db),
        guard_log=GuardLog(db),
        settings_store=store,
    )


async def _until_propose(graph: CompiledStateGraph, deps: GraphDeps, incident: Incident) -> None:
    initial = {
        "incident_id": incident.id,
        "subject": incident.subject,
        "signals": await deps.incidents.signals(incident.id),
        "investigate_rounds": 0,
        "tool_calls_used": 0,
        "instructions": [],
        "verify_loops": 0,
    }
    await graph.ainvoke(initial, IncidentRunner.config(incident.id), interrupt_before=STOP_BEFORE)


async def _peak_prompt_tokens(db: Database, incident_id: str) -> int:
    conn = await db.connect()
    try:
        async with conn.execute(
            "SELECT COALESCE(MAX(tokens_in), 0) AS peak FROM usage WHERE incident_id = ?",
            (incident_id,),
        ) as cursor:
            row = await cursor.fetchone()
    finally:
        await conn.close()
    return int(row["peak"])


async def _collect(
    result: ScenarioResult,
    graph: CompiledStateGraph,
    deps: GraphDeps,
    db: Database,
    incident: Incident,
    fault_on: datetime,
    local: bool,
) -> None:
    """Fill in what the run found and spent, from its checkpoint, events and usage rows."""
    state = (await graph.aget_state(IncidentRunner.config(incident.id))).values
    result.diagnosis = state.get("diagnosis")
    result.proposal = state.get("proposal")
    events = await deps.bus.history(incident.id)
    result.steps = sum(e.kind == "tool_call" for e in events)
    diagnosed = [
        e.at for e in events if e.kind == "node_finished" and e.payload.get("node") == "diagnose"
    ]
    if diagnosed:
        result.seconds_to_diagnosis = (diagnosed[-1] - fault_on).total_seconds()
    total = await deps.ledger.incident_total(incident.id)
    result.tokens = total.tokens
    result.usd = 0.0 if local and total.usd is None else total.usd
    result.peak_prompt_tokens = await _peak_prompt_tokens(db, incident.id)


async def run_scenario(
    scenario: Scenario,
    settings: EvalSettings,
    *,
    faults: Faults,
    judge: Judge,
    run: int = 1,
    connector: Connector = connect_host,
    models: ModelFactory | None = None,
) -> ScenarioResult:
    """One run: fault on, the scenario's incident to the end of Propose, fault off, scored.

    A run that times out or fails keeps what it found so far and says why in `error`. A fault
    that cannot be switched off raises `FaultError`: the next run would meet a broken stack.
    """
    result = ScenarioResult(
        id=scenario.id, run=run, model=tested_model(settings.models), judge=judge.name
    )
    with tempfile.TemporaryDirectory(prefix="bleep-eval-") as folder:
        db = Database(Path(folder) / "bleep.db")
        await db.migrate()
        registry = Registry([await eval_host(settings, connector)], connector)
        await registry.start()
        try:
            async with open_checkpointer(Path(folder) / "checkpoints.sqlite") as saver:
                deps = _graph_deps(db, registry, settings, models)
                graph = build_graph(deps, saver)
                catcher = IncidentCatcher(graph, deps.incidents, deps.bus)
                manager = WatcherManager(
                    registry,
                    Detector(Thresholds(), utcnow),
                    deps.incidents,
                    catcher,
                    deps.bus,
                    guard_log=deps.guard_log,
                    store=UrlCheckStore(db),
                    watch_interval_s=settings.watch_interval_s,
                )
                await manager.start()
                try:
                    await _fault_run(scenario, settings, faults, result, graph, deps, db, catcher)
                finally:
                    await manager.stop()
                    if isinstance(deps.guard_log, GuardLog):
                        await deps.guard_log.flush()
        finally:
            await registry.close()
    if result.diagnosis is not None:
        await _judge(scenario, judge, result)
    return result


async def _fault_run(
    scenario: Scenario,
    settings: EvalSettings,
    faults: Faults,
    result: ScenarioResult,
    graph: CompiledStateGraph,
    deps: GraphDeps,
    db: Database,
    catcher: IncidentCatcher,
) -> None:
    fault_on = utcnow()
    incident: Incident | None = None
    try:
        await faults.switch(scenario.fault, "on")
        async with asyncio.timeout(scenario.timeout_s):
            incident = await catcher.wait_for(scenario.subject)
            result.incident_id = incident.id
            result.seconds_to_incident = (incident.opened_at - fault_on).total_seconds()
            await _until_propose(graph, deps, incident)
    except TimeoutError:
        where = "waiting for the incident" if incident is None else "before Propose ended"
        result.error = f"timed out after {scenario.timeout_s} s {where}"
    except Exception as error:  # the run's failure is a result, not the harness's
        log.exception("scenario %s run %d failed", scenario.id, result.run)
        result.error = f"{type(error).__name__}: {error}"
    finally:
        await faults.switch(scenario.fault, "off")
    if incident is not None:
        await _collect(
            result, graph, deps, db, incident, fault_on, settings.models.provider == "local"
        )
    plan = score_action(scenario.expected, result.proposal)
    result.action_correct, result.unnecessary_action = plan.action_correct, plan.unnecessary_action
    result.forbidden = plan.forbidden
    result.category_correct = score_category(scenario.expected, result.diagnosis)


async def _judge(scenario: Scenario, judge: Judge, result: ScenarioResult) -> None:
    assert result.diagnosis is not None
    try:
        judgement = await judge.judge(scenario.expected.root_cause, result.diagnosis)
    except JudgeError as error:
        result.judge_reason = f"the judge failed: {error}"
        return
    result.root_cause_score = judgement.score
    result.root_cause_pass = judgement.passed
    result.judge_reason = judgement.reason
    result.judge_usd = judgement.usd


def summary(result: ScenarioResult) -> str:
    """One line per finished run, for the console."""
    score = "n/a" if result.root_cause_score is None else f"{result.root_cause_score:.2f}"
    plan = "exact" if result.action_correct else "not exact"
    calls = f"{result.steps} tool call" + ("" if result.steps == 1 else "s")
    if result.unnecessary_action:
        plan += ", unnecessary action"
    line = (
        f"{result.id} run {result.run}: root cause {score}, plan {plan}, {calls}, "
        f"{result.tokens:,} tokens (largest prompt {result.peak_prompt_tokens:,})"
    )
    return line + (f"; {result.error}" if result.error else "")


async def run_suite(
    scenarios: Sequence[Scenario],
    settings: EvalSettings,
    repeat: int,
    *,
    faults: Faults,
    judge: Judge,
    on_result: Callable[[ScenarioResult], None] = lambda _result: None,
    connector: Connector = connect_host,
    models: ModelFactory | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> list[ScenarioResult]:
    """Every scenario `repeat` times, one after another, with `settle_s` between runs."""
    results: list[ScenarioResult] = []
    for scenario in scenarios:
        for run in range(1, repeat + 1):
            if results:
                await sleep(settings.settle_s)
            log.info("scenario %s, run %d of %d", scenario.id, run, repeat)
            result = await run_scenario(
                scenario,
                settings,
                faults=faults,
                judge=judge,
                run=run,
                connector=connector,
                models=models,
            )
            on_result(result)
            results.append(result)
            log.info("%s", summary(result))
    return results


async def demo_containers(
    settings: EvalSettings, connector: Connector = connect_host
) -> list[ContainerSummary]:
    """The demo host's containers now; `EvalError` when its engine cannot be reached."""
    host = await eval_host(settings, connector)
    registry = Registry([host], connector)
    await registry.start()
    try:
        state = registry.hosts()[0]
        listed = registry.snapshot(host.id)
        if not state.reachable or listed is None:
            where = host.endpoint or host.address
            raise EvalError(f"cannot reach the demo host's Docker engine ({where}): {state.error}")
        return listed
    finally:
        await registry.close()


def _commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        )
    except OSError, subprocess.CalledProcessError:
        return None
    return out.stdout.strip() or None


def _command(args: argparse.Namespace) -> str:
    parts = ["uv run --group evals python -m evals.run"]
    if args.model:
        parts.append(f"--model {args.model}")
    if args.only:
        parts.append(f"--only {args.only}")
    parts.append(f"--repeat {args.repeat}")
    return " ".join(parts)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.run", description=__doc__.split("\n")[0])
    parser.add_argument("--model", help="the model under test; default: Bleep's model settings")
    parser.add_argument("--only", help="run one scenario, by id")
    parser.add_argument("--repeat", type=int, default=1, help="runs per scenario (default 1)")
    return parser


async def _run(
    args: argparse.Namespace, settings: EvalSettings, scenarios: list[Scenario], judge: Judge
) -> int:
    if live_backend(await demo_containers(settings)):
        log.warning(
            "Bleep's backend is running on this engine: it will see the faults too, open its own "
            "incidents and page you. Stop it first: docker compose stop backend"
        )
    started = utcnow()
    raw = RAW_DIR / f"scenarios-{started:%Y%m%d-%H%M%S}.jsonl"
    results = await run_suite(
        scenarios,
        settings,
        args.repeat,
        faults=DemoFaults(settings.fault_script),
        judge=judge,
        on_result=lambda result: append_jsonl(raw, result),
    )
    info = RunInfo(
        started_at=started,
        finished_at=utcnow(),
        model=tested_model(settings.models),
        provider=settings.models.provider,
        judge=judge.name,
        repeat=args.repeat,
        scenarios=scenarios,
        commit=_commit(),
        command=_command(args),
        raw=str(raw.relative_to(REPO)),
    )
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report = report_path(started.date(), REPORTS_DIR)
    report.write_text(render(info, results), encoding="utf-8")
    print(f"report: {report.relative_to(REPO)}\nraw data: {info.raw}")
    return 0


def main(argv: Sequence[str] | None = None, settings: EvalSettings | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per model call otherwise
    settings = settings or EvalSettings()
    if args.model:
        settings = settings.with_model(args.model)
    try:
        if args.repeat < 1:
            raise EvalError("--repeat must be 1 or more")
        check_distinct(settings.judge_model, tested_model(settings.models))
        scenarios = load_scenarios(only=args.only)
        if not settings.fault_script.is_file():
            raise EvalError(f"no fault script at {settings.fault_script}")
        judge = build_judge(settings.judge_model, settings.models)
        return asyncio.run(_run(args, settings, scenarios, judge))
    except (EvalError, JudgeError, ScenarioError) as error:
        print(f"evals: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
