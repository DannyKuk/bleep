"""Run fault scenarios on the demo stack and score what Bleep diagnoses and proposes.

    cd backend && uv run --group evals python -m evals.run [--model X] [--judge J] [--only id]
        [--repeat N] [--append raw.jsonl] [--no-reset]

Each run builds Bleep's watcher and incident graph in this process, on a throwaway SQLite
database, with Telegram off, no memory and no act tools, but the MCP plugin's for a scenario that
serves an MCP fixture (`evals/mcp_server.py`), which nothing ever calls. Before each run it
recreates the demo shop's containers, so no earlier run's log reaches this one (#104). It switches
the scenario's fault on with `demo/fault.sh`, waits for the watcher to open the scenario's
incident, runs the graph to the end of Propose and stops there: evals never execute anything.
Then it switches the fault off, has the judge score the root cause (or leaves it for a grade by
hand with the `manual` judge), and checks the category and the plan exactly. Results go to
`data/evals/` as JSONL while the run goes, and to a dated report in `docs/evals/` at the end.
See README.md next to this file and docs/design/evals.md.
"""

import argparse
import asyncio
import contextlib
import logging
import os
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, Self

import httpx
import openai
from langgraph.graph.state import CompiledStateGraph
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from bleep.graph.build import build_graph, open_checkpointer
from bleep.graph.deps import (
    CORE_ACTIONS,
    GraphDeps,
    Memory,
    ModelFactory,
    NullMemory,
    StaticSettings,
    model_factory,
)
from bleep.guard.log import GuardLog
from bleep.hosts.config import HostConfig, ServiceOverrides, load_hosts_file
from bleep.hosts.discovery import is_self
from bleep.hosts.github import GithubClient
from bleep.hosts.registry import Connector, Registry, connect_host
from bleep.incidents.action_log import ActionLog
from bleep.incidents.bus import EventBus
from bleep.incidents.looks import Looks
from bleep.incidents.quiet import QuietStore
from bleep.incidents.repository import IncidentRepo, utcnow
from bleep.incidents.runner import IncidentRunner
from bleep.knowledge.index import KnowledgeIndex
from bleep.knowledge.ingest import load_snapshot, repo_documents
from bleep.knowledge.repos import DEFAULT_DOCS, MARKDOWN
from bleep.llm import structured
from bleep.llm.usage import CostLedger
from bleep.memory.embeddings import Embedder, FastEmbedEmbedder
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.github import RepoFile
from bleep.schemas.incidents import Incident, Subject
from bleep.schemas.knowledge import KnowledgeDocument
from bleep.schemas.services import Service
from bleep.schemas.settings import ModelSettings, Thresholds
from bleep.store.db import Database
from bleep.tools.default import default_registry
from bleep.tools.knowledge import Searcher
from bleep.watcher.detector import Detector
from bleep.watcher.http_checks import UrlCheckStore
from bleep.watcher.manager import WatcherManager
from evals.github import fixture_client, load_github_fixture
from evals.judge import DEFAULT_JUDGE, MANUAL, Judge, JudgeError, build_judge, check_distinct
from evals.judge import model_under_test as tested_model
from evals.mcp_server import ServedMcp, load_mcp_fixture, serve
from evals.report import (
    RunInfo,
    append_jsonl,
    read_info,
    read_jsonl,
    render,
    report_path,
    write_info,
)
from evals.reset_spans import ResetSpans, hide_resets
from evals.scenarios import (
    Scenario,
    ScenarioError,
    ScenarioResult,
    ToolCallRecord,
    load_scenarios,
    score_action,
    score_category,
    score_names,
)

log = logging.getLogger("evals")

REPO = Path(__file__).resolve().parents[2]
ENV_FILE = REPO / ".env"
REPORTS_DIR = REPO / "docs" / "evals"
RAW_DIR = REPO / "data" / "evals"
# The graph stops before these nodes: Approval is where a plan would be put to a human and then
# executed, and Report would write to memory. Propose is the last node an eval runs, but for a
# look that proposed no action: its report is part of the answer (#410), and memory is off.
STOP_BEFORE = ["approval", "report"]
LOOK_POLL_S = 1.0
FAULT_TIMEOUT_S = 300
COMPOSE_SERVICE = "com.docker.compose.service"
DOCKER_DESKTOP_SOCKET = Path.home() / ".docker" / "run" / "docker.sock"
LOCAL_NAMES = ("localhost", "127.0.0.1")
DEMO_COMPOSE = REPO / "demo" / "compose.yaml"
SHOP_PROJECT = "shop"  # demo/compose.yaml's `name:`
# The GitHub repo this checkout is. The demo hosts file links the shop to it, so the harness reads
# the shop's docs from here, at the paths the hosts file names, and asks GitHub for nothing.
CHECKOUT_REPO = "DannyKuk/bleep"
# The shop's containers with a health check; the reset waits until both are healthy.
SHOP_HEALTHY = ("shop-web", "shop-db")
ITEMS_PATH = "/api/items"
RESET_TIMEOUT_S = 180
RESET_POLL_S = 3


def _from_this_machine(url: str) -> str:
    """A URL as this machine reaches it: `host.docker.internal` is a container's name for this
    machine, so it becomes `localhost`. The demo shop's URLs go through shop-proxy's published
    port, 8080, so `http://host.docker.internal:8080` is `http://localhost:8080` here."""
    parsed = httpx.URL(url)
    if parsed.host == "host.docker.internal":
        return str(parsed.copy_with(host="localhost"))
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


# Called after Propose, while the run's database and watcher are still open, with the fault off.
# The injection tests (evals/injection.py) use it to take a run on to its report.
AfterPropose = Callable[[CompiledStateGraph, GraphDeps, ScenarioResult], Awaitable[None]]


# Runs a command with this stdin and environment: (exit code, stdout, stderr).
Runner = Callable[[list[str], str, dict[str, str]], Awaitable[tuple[int, str, str]]]


async def _exec(argv: list[str], stdin: str, env: dict[str, str]) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    out, err = await process.communicate(stdin.encode())
    return process.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


class ShopReset:
    """Puts the demo shop back to a fresh start before a run (#104): every container recreated
    from `demo/compose.yaml` (`up -d --force-recreate`), so every log starts empty, with the
    volumes kept, so shop-db keeps its items. It returns once shop-web and shop-db report
    healthy and the shop's item URL answers 200, and raises `EvalError` when the recreate fails
    or the shop is not back within `timeout_s`: the next run would meet a broken stack.

    `fault.sh all off` alone is not enough: it never recreates shop-db, whose log then keeps
    an earlier fault's errors. `docker_host`, as for the fault script, is the engine the
    harness watches, and `env` adds to the environment Compose reads (the injection tests'
    hook and key). With `spans`, each reset's time span is recorded there, so the engine the
    run reads can hide the reset's own events (#110)."""

    def __init__(
        self,
        compose_file: Path,
        items_url: str,
        *,
        docker_host: str | None = None,
        env: Mapping[str, str] | None = None,
        run: Runner = _exec,
        http: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] | None = None,
        timeout_s: float = RESET_TIMEOUT_S,
        spans: ResetSpans | None = None,
    ) -> None:
        self.spans = spans
        self.compose_file = compose_file
        self.items_url = items_url
        self.env = os.environ | dict(env or {})
        if docker_host:
            self.env["DOCKER_HOST"] = docker_host
        self.timeout_s = timeout_s
        self._run = run
        self._http = http
        self._sleep = sleep
        self._clock = clock

    async def __call__(self) -> None:
        started = utcnow()
        try:
            await self._recreate()
        finally:
            if self.spans is not None:
                self.spans.record(started, utcnow())

    async def _recreate(self) -> None:
        # `-p shop`: a COMPOSE_PROJECT_NAME in the environment must not aim it at another project.
        up = ["docker", "compose", "-p", SHOP_PROJECT, "-f", str(self.compose_file)]
        up += ["up", "-d", "--force-recreate"]
        try:
            code, _out, err = await asyncio.wait_for(self._run(up, "", self.env), self.timeout_s)
        except TimeoutError:
            raise EvalError(f"recreating the shop took over {self.timeout_s:.0f} s") from None
        if code != 0:
            raise EvalError(f"recreating the shop failed: {err.strip()[-500:]}")
        async with contextlib.AsyncExitStack() as stack:
            http = self._http or await stack.enter_async_context(httpx.AsyncClient(timeout=10))
            await self._until_ready(http)

    async def _until_ready(self, http: httpx.AsyncClient) -> None:
        inspect = ["docker", "inspect", "-f", "{{.State.Health.Status}}", *SHOP_HEALTHY]
        clock = self._clock or asyncio.get_running_loop().time
        deadline, why = clock() + self.timeout_s, ""
        while clock() <= deadline:  # a deadline, so a slow `docker` counts too
            await self._sleep(RESET_POLL_S)
            code, out, err = await self._run(inspect, "", self.env)
            health = out.split()
            if code != 0:
                why = err.strip()[-200:]
                continue
            if health != ["healthy"] * len(SHOP_HEALTHY):
                why = ", ".join(f"{n} {s}" for n, s in zip(SHOP_HEALTHY, health, strict=False))
                continue
            try:
                answer = await http.get(self.items_url)
            except httpx.HTTPError as error:
                why = f"{self.items_url} did not answer: {error}"
                continue
            if answer.status_code == 200:
                return
            why = f"{self.items_url} answered {answer.status_code}"
        raise EvalError(f"the shop is not back after {self.timeout_s:.0f} s: {why}")


def shop_items_url(host: HostConfig) -> str:
    """The URL the reset waits on: the host's URL check on the shop's item list."""
    for service in host.services.values():
        for check in service.urls:
            if httpx.URL(check.url).path == ITEMS_PATH:
                return check.url
    raise EvalError(
        f"host {host.id} checks no {ITEMS_PATH} URL for the reset to wait on; run with --no-reset"
    )


class DemoFaults:
    """`demo/fault.sh <fault> on|off`, run from the repo root. The script runs compose on its own
    file and acts on the engine `docker` points at, so `docker_host`, when given, is its
    `DOCKER_HOST`: the engine the harness watches. It exits 0 once the fault is switched, 2 on a
    fault or state it does not know, and non-zero when a docker command fails. `env` adds to its
    environment, which Compose reads when the script recreates a container."""

    def __init__(
        self,
        script: Path,
        docker_host: str | None = None,
        timeout_s: float = FAULT_TIMEOUT_S,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.script = script
        self.docker_host = docker_host
        self.timeout_s = timeout_s
        self.env = dict(env or {})

    async def switch(self, fault: str, state: Literal["on", "off"]) -> None:
        what = f"{self.script.name} {fault} {state}"
        env = os.environ | self.env
        env |= {"DOCKER_HOST": self.docker_host} if self.docker_host else {}
        process = await asyncio.create_subprocess_exec(
            str(self.script),
            fault,
            state,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=REPO,
            env=env,
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


def eval_host(settings: EvalSettings) -> HostConfig:
    """The demo host from the hosts file, reached from this machine. A socket host talks to the
    local engine instead of the proxy inside Compose, and a URL for `host.docker.internal` goes
    to `localhost`. Rules, protected containers and the rest stay as the file says. A URL on a
    bare name, as a container is named on a Compose network, does not answer from here: it is
    logged, since its checks fail and could open an incident of their own."""
    hosts = load_hosts_file(settings.hosts_file).hosts
    host = next((h for h in hosts if h.id == settings.host_id), None)
    if host is None:
        known = ", ".join(h.id for h in hosts)
        raise EvalError(f"no host {settings.host_id!r} in {settings.hosts_file}; known: {known}")
    if host.kind == "socket":
        host = host.model_copy(update={"endpoint": docker_endpoint(settings)})
    services: dict[str, ServiceOverrides] = {}
    for name, service in host.services.items():
        moved = [u.model_copy(update={"url": _from_this_machine(u.url)}) for u in service.urls]
        for check in moved:
            host_name = httpx.URL(check.url).host
            if host_name not in LOCAL_NAMES and "." not in host_name:
                log.warning("%s may not answer from this machine; its checks may fail", check.url)
        services[name] = service.model_copy(update={"urls": moved})
    return host.model_copy(update={"services": services})


def fault_docker_host(settings: EvalSettings, host: HostConfig) -> str | None:
    """The `DOCKER_HOST` the fault script gets, so it breaks the engine the harness watches: a
    socket host's engine as this machine reaches it. For an SSH host it is None, and the
    script acts on whatever `docker` points at (docs/demo.md, "On another host")."""
    return docker_endpoint(settings) if host.kind == "socket" else None


def live_backend(containers: Sequence[ContainerSummary]) -> bool:
    """Whether Bleep's own backend is running on this engine: it would see the fault too, open
    its own incident and page the human."""
    return any(
        is_self(c.labels) and c.labels.get(COMPOSE_SERVICE) == "backend" and c.state == "running"
        for c in containers
    )


class _FallbackCounter(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        if record.msg == structured.FALLBACK:
            self.count += 1


@contextlib.contextmanager
def counting_fallbacks() -> Iterator[_FallbackCounter]:
    """Counts the structured-output fallbacks while it is open: model calls that gave no
    structured output through tools, so Bleep asked for JSON in the reply instead
    (`bleep.llm.structured`). Runs go one at a time, so the count is the run's."""
    counter, logger = _FallbackCounter(), structured.log
    level = logger.level
    logger.addHandler(counter)
    if not logger.isEnabledFor(logging.INFO):
        logger.setLevel(logging.INFO)
    try:
        yield counter
    finally:
        logger.removeHandler(counter)
        logger.setLevel(level)


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

    async def wait_for(self, subject: Subject, since: datetime) -> Incident:
        """The first incident on `subject` opened at `since` or later; one opened before it (a
        leftover of the last run) is not this run's."""
        while True:
            incident = await self.opened.get()
            if incident.subject.key == subject.key and incident.opened_at >= since:
                return incident
            log.info("ignoring incident %s on %s", incident.id, incident.subject.key)


def _graph_deps(
    db: Database,
    registry: Registry,
    settings: EvalSettings,
    models: ModelFactory | None,
    memory: Memory | None = None,
    github: GithubClient | None = None,
    knowledge: Searcher | None = None,
    mcp: ServedMcp | None = None,
) -> GraphDeps:
    """Bleep's graph parts, minus everything that reaches past Propose or out of the process:
    no act tools but the MCP plugin's when the run serves its server (`evals.mcp_server`; Execute
    never runs, so none is called), no memory unless a test plants one (each run starts blank),
    no Telegram, no GitHub tools unless the run serves a fixture (`evals.github`), and no
    knowledge search unless a case gives one."""
    guard_log = GuardLog(db)
    plugins = mcp.plugins(guard_log) if mcp is not None else None
    actions = CORE_ACTIONS | (plugins.actions() if plugins is not None else set())
    store = StaticSettings(model_settings=settings.models, actions=actions)
    return GraphDeps(
        registry=registry,
        tools=default_registry(plugins=plugins, github=github, knowledge=knowledge),
        act_tools=plugins.act_tools({}) if plugins is not None else {},
        models=models or model_factory(store),
        bus=EventBus(db),
        ledger=CostLedger(db),
        incidents=IncidentRepo(db),
        action_log=ActionLog(db),
        guard_log=guard_log,
        settings_store=store,
        memory=memory or NullMemory(),
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
    result.tool_calls = [
        ToolCallRecord.of(e.payload.get("name"), e.payload.get("args"))
        for e in events
        if e.kind == "tool_call"
    ]
    result.steps = len(result.tool_calls)
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
    judge: Judge | None,
    run: int = 1,
    connector: Connector = connect_host,
    models: ModelFactory | None = None,
    memory: Memory | None = None,
    knowledge: Searcher | None = None,
    after: AfterPropose | None = None,
    github: GithubClient | None = None,
    mcp: ServedMcp | None = None,
    make_registry: Callable[[list[HostConfig], Connector], Registry] = Registry,
) -> ScenarioResult:
    """One run: fault on, the scenario's incident to the end of Propose, fault off, scored.

    A run that times out or fails keeps what it found so far and says why in `error`. A fault
    that cannot be switched off raises `FaultError`: the next run would meet a broken stack.
    Without a judge the root cause is not scored. `memory` is what Gather recalls (none by
    default), and `after` runs once Propose ended without a failure; a failure in it is the
    run's, like one before. `github` is the GitHub the tools read; without it they are absent,
    and a scenario with a `github` fixture gets one dated from the run's start. `mcp` is the MCP
    server the plugin is switched on for; without it, a scenario with an `mcp` fixture gets that
    fixture served in this process for the run, and one without has the plugin off.
    `knowledge` is what the `knowledge_search` tool searches (no tool by default).
    `make_registry` builds the host registry, so an injection case can plant text in what it
    reads from the host (`evals.injection.PlantedBasics`).
    """
    result = ScenarioResult(
        id=scenario.id,
        run=run,
        model=tested_model(settings.models),
        judge=judge.name if judge is not None else "none",
    )
    async with contextlib.AsyncExitStack() as stack:
        fallbacks = stack.enter_context(counting_fallbacks())
        folder = stack.enter_context(tempfile.TemporaryDirectory(prefix="bleep-eval-"))
        if mcp is None and scenario.mcp is not None:
            mcp = await stack.enter_async_context(serve(load_mcp_fixture(scenario.mcp)))
        db = Database(Path(folder) / "bleep.db")
        await db.migrate()
        registry = make_registry([eval_host(settings)], connector)
        await registry.start()
        try:
            async with open_checkpointer(Path(folder) / "checkpoints.sqlite") as saver:
                if github is None and scenario.github is not None:
                    github = fixture_client(load_github_fixture(scenario.github))
                deps = _graph_deps(db, registry, settings, models, memory, github, knowledge, mcp)
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
                    await _fault_run(
                        scenario, settings, faults, result, graph, deps, db, catcher, registry
                    )
                    if scenario.look and result.incident_id and result.failure is None:
                        try:
                            await _report_look(graph, deps, result)
                        except Exception as error:  # as in _fault_run: a result, not a crash
                            _failed(scenario, result, error)
                    if after is not None and result.incident_id and result.failure is None:
                        try:
                            await after(graph, deps, result)
                        except Exception as error:  # as in _fault_run: a result, not a crash
                            _failed(scenario, result, error)
                finally:
                    await manager.stop()
                    if isinstance(deps.guard_log, GuardLog):
                        await deps.guard_log.flush()
        finally:
            await registry.close()
    result.structured_fallbacks = fallbacks.count
    if result.diagnosis is not None and judge is not None:
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
    registry: Registry,
) -> None:
    fault_on = utcnow()
    incident: Incident | None = None
    try:
        if scenario.fault is not None:
            await faults.switch(scenario.fault, "on")
        async with asyncio.timeout(scenario.timeout_s):
            if scenario.look:
                await ask_for_look(scenario.subject, registry, db, catcher)
            incident = await catcher.wait_for(scenario.subject, since=fault_on)
            result.incident_id = incident.id
            result.seconds_to_incident = (incident.opened_at - fault_on).total_seconds()
            await _until_propose(graph, deps, incident)
    except TimeoutError:
        where = "waiting for the incident" if incident is None else "before Propose ended"
        result.failure = "timeout"
        result.error = f"timed out after {scenario.timeout_s} s {where}"
    except Exception as error:  # the run's failure is a result, not the harness's
        _failed(scenario, result, error)
    finally:
        if scenario.fault is not None:
            await faults.switch(scenario.fault, "off")
    if incident is not None:
        await _collect(
            result, graph, deps, db, incident, fault_on, settings.models.provider == "local"
        )
    plan = score_action(scenario.expected, result.proposal)
    result.action_correct, result.unnecessary_action = plan.action_correct, plan.unnecessary_action
    result.forbidden = plan.forbidden
    result.category_correct = score_category(scenario.expected, result.diagnosis)
    names = score_names(scenario.expected, result.diagnosis, result.proposal)
    result.diagnosis_unnamed, result.plan_unnamed = names.diagnosis_unnamed, names.plan_unnamed


def healthy(service: Service | None) -> bool:
    """Whether the watcher's last listing shows every container of the service running, none
    of them unhealthy or still starting."""
    return service is not None and all(
        c.state == "running" and c.health in ("healthy", "none") for c in service.containers
    )


async def ask_for_look(
    subject: Subject, registry: Registry, db: Database, catcher: IncidentCatcher
) -> None:
    """Once the service is healthy, ask for a look at it as a person's tap does
    (`incidents.looks.Looks`, #403): the incident opens through the watcher's path and reaches
    the catcher like any other."""
    service_id = subject.service_id or ""
    while not healthy(registry.service(service_id)):
        await asyncio.sleep(LOOK_POLL_S)
    looks = Looks(IncidentRepo(db), catcher, QuietStore(db), registry)
    look = await looks.look(service_id, "dashboard")
    if look.outcome != "opened":
        raise EvalError(f"the look at {service_id} opened no incident: {look.outcome}")


async def _report_look(graph: CompiledStateGraph, deps: GraphDeps, result: ScenarioResult) -> None:
    """A look whose plan is no action goes on to Report, as Bleep's own run does, and its
    report is kept; a plan that acts waits for Approval, which an eval never gives. The report's
    model call counts in the run's tokens and cost."""
    assert result.incident_id is not None
    config = IncidentRunner.config(result.incident_id)
    if (await graph.aget_state(config)).next != ("report",):
        return
    await graph.ainvoke(None, config)
    result.report = (await graph.aget_state(config)).values.get("report")
    total = await deps.ledger.incident_total(result.incident_id)
    result.tokens = total.tokens
    if result.usd is not None and total.usd is not None:
        result.usd = total.usd


def _failed(scenario: Scenario, result: ScenarioResult, error: Exception) -> None:
    if out_of_context(error):
        log.warning("scenario %s run %d ran out of context: %s", scenario.id, result.run, error)
        result.failure = "context_length"
    else:
        log.exception("scenario %s run %d failed", scenario.id, result.run)
        result.failure = "error"
    result.error = f"{type(error).__name__}: {error}"


def out_of_context(error: BaseException) -> bool:
    """Whether a model call ended on its length limit before it answered: openai's
    `LengthFinishReasonError`, raised by a structured call whose reply was cut off. A reasoning
    model with a small context can spend all of it thinking (docs/demo.md, "Full runs"). It is
    looked for in the error's causes too, and in an exception group's members."""
    seen: set[int] = set()
    pending: list[BaseException] = [error]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, openai.LengthFinishReasonError):
            return True
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        pending.extend(e for e in (current.__cause__, current.__context__) if e is not None)
    return False


async def _judge(scenario: Scenario, judge: Judge, result: ScenarioResult) -> None:
    assert result.diagnosis is not None
    try:
        judgement = await judge.judge(scenario.expected.root_cause, result.diagnosis)
    except JudgeError as error:
        result.judge_reason = f"the judge failed: {error}"
        return
    if judgement is None:  # graded by hand later
        return
    result.root_cause_score = judgement.score
    result.root_cause_pass = judgement.passed
    result.judge_reason = judgement.reason
    result.judge_usd = judgement.usd


def summary(result: ScenarioResult) -> str:
    """One line per finished run, for the console."""
    if result.root_cause_score is not None:
        score = f"{result.root_cause_score:.2f}"
    elif result.diagnosis is not None and result.judge == MANUAL:
        score = "pending"
    else:
        score = "n/a"
    plan = "exact" if result.action_correct else "not exact"
    calls = f"{result.steps} tool call" + ("" if result.steps == 1 else "s")
    if result.unnecessary_action:
        plan += ", unnecessary action"
    for where, left_out in (("diagnosis", result.diagnosis_unnamed), ("plan", result.plan_unnamed)):
        if left_out:
            plan += f", the {where} does not name {', '.join(left_out)}"
    line = (
        f"{result.id} run {result.run}: root cause {score}, plan {plan}, {calls}, "
        f"{result.tokens:,} tokens (largest prompt {result.peak_prompt_tokens:,})"
    )
    if result.structured_fallbacks:
        n = result.structured_fallbacks
        line += f", {n} structured-output fallback" + ("" if n == 1 else "s")
    if result.failure == "context_length":
        return line + f"; failed, out of context: {result.error}"
    return line + (f"; {result.error}" if result.error else "")


async def snapshot_index(
    folder: Path, embedder: Embedder, repo_docs: Sequence[KnowledgeDocument] = ()
) -> KnowledgeIndex:
    """Knowledge search as a fresh install has it once its repos are read: the shipped Docker
    docs snapshot and `repo_docs`, embedded into a database in `folder`, with no past
    incidents."""
    db = Database(folder / "knowledge.db")
    await db.migrate()
    index = KnowledgeIndex(db, embedder)
    await index.sync("docker", load_snapshot())
    if repo_docs:
        await index.sync("repo", repo_docs)
    return index


def checkout_repo_docs(host: HostConfig, root: Path = REPO) -> list[KnowledgeDocument]:
    """The repo docs the app would read for the services of `host` that link this checkout's
    repo (`CHECKOUT_REPO`), read from `root`: the README when a service wants it, then the
    markdown under each service's `docs_paths` (`docs` when it names none). A service linking
    another repo gets none, since the harness reads no GitHub; a path missing here is skipped,
    as GitHub's 404 is."""
    paths: list[str] = []
    for name, service in host.services.items():
        if service.repo is None:
            continue
        if service.repo != CHECKOUT_REPO:
            log.info("service %s links %s; its docs are not read", name, service.repo)
            continue
        if service.readme:
            paths.append("README.md")
        for path in service.docs_paths or DEFAULT_DOCS:
            where = root / path
            if where.is_dir():
                found = (p for p in where.rglob("*") if p.suffix.lower() in MARKDOWN)
                paths += sorted(p.relative_to(root).as_posix() for p in found)
            elif path.lower().endswith(MARKDOWN):
                paths.append(path)
    files = [
        RepoFile(path=path, sha="checkout", text=(root / path).read_text(encoding="utf-8"))
        for path in dict.fromkeys(paths)
        if (root / path).is_file()
    ]
    return repo_documents(CHECKOUT_REPO, files)


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
    done: Mapping[str, int] | None = None,
    reset: Callable[[], Awaitable[None]] | None = None,
    knowledge: Searcher | None = None,
) -> list[ScenarioResult]:
    """Every scenario `repeat` times, one after another, with `settle_s` between runs. `done`
    is how many runs each scenario already has from an earlier batch: its runs are numbered
    after them. `reset` (a `ShopReset`) runs before every run instead of the settle wait: it
    returns once the shop answers again. `knowledge` is what `knowledge_search` searches."""
    done = done or {}
    results: list[ScenarioResult] = []
    for scenario in scenarios:
        first = done.get(scenario.id, 0) + 1
        for run in range(first, first + repeat):
            if reset is not None:
                log.info("resetting the shop")
                await reset()
            elif results:
                await sleep(settings.settle_s)
            log.info("scenario %s, run %d of %d", scenario.id, run, first + repeat - 1)
            result = await run_scenario(
                scenario,
                settings,
                faults=faults,
                judge=judge,
                run=run,
                connector=connector,
                models=models,
                knowledge=knowledge,
            )
            on_result(result)
            results.append(result)
            log.info("%s", summary(result))
    return results


async def demo_containers(
    settings: EvalSettings, connector: Connector = connect_host
) -> list[ContainerSummary]:
    """The demo host's containers now; `EvalError` when its engine cannot be reached."""
    host = eval_host(settings)
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


def earlier_batch(raw: Path, now: datetime, model: str) -> tuple[list[ScenarioResult], datetime]:
    """The runs an earlier batch wrote to `raw`, and when that batch started, read from the
    file's name (`scenarios-<YYYYMMDD-HHMMSS>.jsonl`, in UTC), else `now`. A suite can run in
    batches this way, one repeat at a time, and still end in one report. Runs of another model
    under test are refused: one report measures one model."""
    if not raw.is_file():
        raise EvalError(f"no earlier batch at {raw}")
    earlier = read_jsonl(raw)
    others = sorted({r.model for r in earlier} - {model})
    if others:
        raise EvalError(f"{raw} has runs of {', '.join(others)}, not of {model}")
    try:
        started = datetime.strptime(raw.stem, "scenarios-%Y%m%d-%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        started = now
    return earlier, started


def suite_scenarios(raw: Path, scenarios: Sequence[Scenario]) -> list[Scenario]:
    """The scenarios a suite's report covers: an earlier batch's (from the raw file's
    `.info.json`), then this batch's new ones. A suite run one scenario per batch (`--only` with
    `--append`) so still reports on every one of them."""
    earlier = read_info(raw)
    covered = list(earlier.scenarios) if earlier is not None else []
    known = {s.id for s in covered}
    return covered + [s for s in scenarios if s.id not in known]


def report_for(raw: Path, started: datetime, reports_dir: Path) -> Path:
    """The report a batch writes: the one an earlier batch of the same suite wrote (named in
    the raw file's `.info.json`), so the suite ends in one report, else a new dated one."""
    earlier = read_info(raw)
    if earlier is not None and earlier.report:
        return Path(earlier.report) if Path(earlier.report).is_absolute() else REPO / earlier.report
    return report_path(started.date(), reports_dir)


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
    if args.judge:
        parts.append(f"--judge {args.judge}")
    if args.only:
        parts.append(f"--only {args.only}")
    parts.append(f"--repeat {args.repeat}")
    if args.append:
        parts.append(f"--append {args.append}")
    if args.no_reset:
        parts.append("--no-reset")
    if args.no_knowledge:
        parts.append("--no-knowledge")
    return " ".join(parts)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.run", description=__doc__.split("\n")[0])
    parser.add_argument("--model", help="the model under test; default: Bleep's model settings")
    parser.add_argument(
        "--judge",
        help="the root cause judge, as EVAL_JUDGE_MODEL: an OpenRouter model id, "
        "claude-code[:<model>], or manual (graded by hand afterwards with evals.report)",
    )
    parser.add_argument("--only", help="run one scenario, by id")
    parser.add_argument(
        "--no-knowledge", action="store_true",
        help="do not offer knowledge_search (the app offers it over the Docker docs snapshot "
        "and the linked repo docs)",
    )  # fmt: skip
    parser.add_argument("--repeat", type=int, default=1, help="runs per scenario (default 1)")
    parser.add_argument(
        "--append",
        metavar="JSONL",
        help="continue an earlier batch: add these runs to its raw file (a path from the repo "
        "root), numbered after its runs, and report on all of them",
    )
    parser.add_argument(
        "--no-reset",
        action="store_true",
        help="do not recreate the shop's containers before each run; wait EVAL_SETTLE_S instead",
    )
    return parser


async def _run(
    args: argparse.Namespace,
    settings: EvalSettings,
    scenarios: list[Scenario],
    judge: Judge,
    batch: tuple[list[ScenarioResult], datetime],
) -> int:
    """`batch` is the earlier runs this one continues (`--append`) and when the suite started:
    none and now for a new suite."""
    if live_backend(await demo_containers(settings)):
        log.warning(
            "Bleep's backend is running on this engine: it will see the faults too, open its own "
            "incidents and page you. Stop it first: docker compose stop backend"
        )
    earlier, started = batch
    raw = (
        REPO / args.append if args.append else RAW_DIR / f"scenarios-{started:%Y%m%d-%H%M%S}.jsonl"
    )
    done: dict[str, int] = {}
    for r in earlier:
        done[r.id] = max(done.get(r.id, 0), r.run)
    host = eval_host(settings)
    docker_host = fault_docker_host(settings, host)
    spans = ResetSpans()
    reset = None if args.no_reset else ShopReset(
        DEMO_COMPOSE, shop_items_url(host), docker_host=docker_host, spans=spans
    )  # fmt: skip
    with tempfile.TemporaryDirectory(prefix="bleep-knowledge-") as folder:
        knowledge = None
        repo_docs = [] if args.no_knowledge else checkout_repo_docs(host)
        if not args.no_knowledge:
            log.info(
                "embedding the Docker docs snapshot and %d repo docs for knowledge search",
                len(repo_docs),
            )
            knowledge = await snapshot_index(Path(folder), FastEmbedEmbedder(), repo_docs)
        results = await run_suite(
            scenarios,
            settings,
            args.repeat,
            faults=DemoFaults(settings.fault_script, docker_host),
            judge=judge,
            on_result=lambda result: append_jsonl(raw, result),
            done=done,
            reset=reset,
            knowledge=knowledge,
            connector=hide_resets(connect_host, spans),
        )
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report = report_for(raw, started, REPORTS_DIR)
    info = RunInfo(
        started_at=started,
        finished_at=utcnow(),
        model=tested_model(settings.models),
        provider=settings.models.provider,
        judge=judge.name,
        repeat=max(Counter(r.id for r in earlier + results).values(), default=args.repeat),
        scenarios=suite_scenarios(raw, scenarios),
        commit=_commit(),
        command=_command(args),
        reset_shop=reset is not None,
        knowledge=not args.no_knowledge,
        repo_docs=[d.title for d in repo_docs],
        raw=os.path.relpath(raw, report.parent),
        report=str(report.relative_to(REPO)) if report.is_relative_to(REPO) else str(report),
    )
    write_info(raw, info)
    results = earlier + results
    report.write_text(render(info, results), encoding="utf-8")
    print(f"report: {info.report}\nraw data: {raw}")
    return 0


def main(argv: Sequence[str] | None = None, settings: EvalSettings | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per model call otherwise
    settings = settings or EvalSettings()
    if args.model:
        settings = settings.with_model(args.model)
    if args.judge:
        settings = settings.model_copy(update={"judge_model": args.judge})
    try:
        if args.repeat < 1:
            raise EvalError("--repeat must be 1 or more")
        check_distinct(settings.judge_model, tested_model(settings.models))
        scenarios = load_scenarios(only=args.only)
        if not settings.fault_script.is_file():
            raise EvalError(f"no fault script at {settings.fault_script}")
        now, model = utcnow(), tested_model(settings.models)
        batch = earlier_batch(REPO / args.append, now, model) if args.append else ([], now)
        judge = build_judge(settings.judge_model, settings.models)
        return asyncio.run(_run(args, settings, scenarios, judge, batch))
    except (EvalError, JudgeError, ScenarioError) as error:
        print(f"evals: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
