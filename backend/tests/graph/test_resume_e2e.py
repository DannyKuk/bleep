"""Crash-safe resume, end to end: Bleep dies at a named point of an incident, and a new process
on the same database and checkpoint file finishes it without running any action twice.

Everything outside Bleep outlives the crash: the host (`FakeEngine`), the model's script, the
service's URL and the clock. Everything Bleep holds in memory is built again for each process:
the database handles, the bus, the approvals store, the action log, the registry, the graph and
the runner. A process dies by being cut off where it stands (its runner's tasks are cancelled,
as on a kill), at a point a hook names: inside an engine call, in the middle of Verify's wait,
or while publishing an event. Review Focus 1, issue #28.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import httpx
from langchain_core.messages import AIMessage

from bleep.graph.build import build_graph, open_checkpointer
from bleep.graph.deps import GraphDeps, StaticSettings
from bleep.hosts.config import HostConfig, ServiceOverrides, UrlCheckConfig
from bleep.hosts.registry import Registry
from bleep.incidents.action_log import ActionLog
from bleep.incidents.approvals import ApprovalStore
from bleep.incidents.bus import EventBus
from bleep.incidents.repository import IncidentRepo
from bleep.incidents.runner import IncidentRunner
from bleep.llm.usage import CostLedger
from bleep.schemas.graph import Decision
from bleep.schemas.incidents import Incident, Signal, Subject
from bleep.schemas.settings import Budgets
from bleep.store.db import Database
from bleep.tools.default import default_registry
from bleep.tools.docker_act import docker_act_tools
from fakes import FakeChatModel, FakeEngine, FakeShell
from graph_env import SECRET, T0, RecordingGuardLog, box, no_wait, shop_host

URL = "http://shop.test/health"
DIAGNOSIS = {"root_cause": "shop-web-1 exited", "category": "unknown", "confidence": 0.9}
REPORT = {"summary": "shop-web-1 was restarted.", "cause": "It exited.", "follow_ups": []}


class Crash:
    """A point at which Bleep dies: the run that reaches it signals, then never goes on, until
    the process is cut off. A point is passed once; the next process goes through it."""

    def __init__(self) -> None:
        self.armed = True
        self.reached = asyncio.Event()

    async def here(self) -> None:
        if not self.armed:
            return
        self.armed = False
        self.reached.set()
        await asyncio.Event().wait()  # cancelled when the process is cut off


class Host(FakeEngine):
    """The host. A restart can be made to kill Bleep just before or just after the engine call;
    the container's state stays as the call left it, whatever happened to Bleep."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.before_restart: Crash | None = None
        self.after_restart: Crash | None = None

    async def restart(self, container: str) -> None:
        if self.before_restart is not None:
            await self.before_restart.here()
        await super().restart(container)
        if self.after_restart is not None:
            await self.after_restart.here()

    def restarts(self) -> list[str]:
        return [a.container for a in self.acts if a.action == "restart"]


class DyingBus(EventBus):
    """Kills Bleep as it is about to publish its first event of `kind` whose payload holds every
    item of `like`, before the event is stored."""

    def __init__(self, db: Database, kind: str, crash: Crash, **like: str) -> None:
        super().__init__(db)
        self.kind = kind
        self.like = like
        self.crash = crash

    async def publish(self, incident_id, kind, payload=None):
        if kind == self.kind and self.like.items() <= (payload or {}).items():
            await self.crash.here()
        return await super().publish(incident_id, kind, payload)


class Ticks:
    """The clock the host and the action log share: one second later on every read."""

    def __init__(self) -> None:
        self.now = T0

    def __call__(self):
        self.now += timedelta(seconds=1)
        return self.now


@dataclass
class World:
    """What is still there after Bleep dies."""

    tmp: Path
    host: Host
    model: FakeChatModel
    clock: Ticks
    config: HostConfig = field(default_factory=shop_host)
    budgets: Budgets = field(default_factory=Budgets)
    answering: Callable[[], bool] = lambda: True  # whether the service's URL answers

    def urls(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200 if self.answering() else 503)


@dataclass
class Bleep:
    """One process of Bleep."""

    repo: IncidentRepo
    bus: EventBus
    approvals: ApprovalStore
    action_log: ActionLog
    runner: IncidentRunner

    async def nodes(self, incident_id: str, kind: str = "node_started") -> list[str]:
        return [e.payload["node"] for e in await self.bus.history(incident_id) if e.kind == kind]

    async def events(self, incident_id: str, kind: str) -> list[dict]:
        return [e.payload for e in await self.bus.history(incident_id) if e.kind == kind]

    async def status(self, incident_id: str) -> str:
        return (await self.repo.get(incident_id)).status

    async def values(self, incident_id: str) -> dict:
        return await self.runner.state(incident_id)

    async def approve(self, incident_id: str) -> str:
        """The human says yes on the dashboard: recorded first, then the run resumed."""
        approval_id = await self.runner.waiting_for(incident_id)
        assert approval_id is not None
        decision = Decision(kind="approve", via="dashboard", at=T0)
        assert await self.approvals.resolve(incident_id, decision, approval_id) == decision
        await self.runner.resume(incident_id, decision, approval_id)
        return approval_id


@asynccontextmanager
async def bleep(
    world: World,
    *,
    sleep: Callable[[float], Awaitable[None]] = no_wait,
    bus: Callable[[Database], EventBus] = EventBus,
) -> AsyncIterator[Bleep]:
    """Start a process of Bleep on the world's files; leaving the block cuts it off where it
    stands, as a kill would."""
    db = Database(world.tmp / "bleep.sqlite")
    await db.migrate()

    async def connect(_host: HostConfig) -> tuple[FakeShell, Host]:
        return FakeShell("linux"), world.host

    registry = Registry([world.config], connect)  # type: ignore[arg-type]
    await registry.start()
    repo = IncidentRepo(db)
    events = bus(db)
    approvals = ApprovalStore(db, SECRET)
    action_log = ActionLog(db, clock=world.clock)
    guard_log = RecordingGuardLog()
    http = httpx.AsyncClient(transport=httpx.MockTransport(world.urls))
    deps = GraphDeps(
        registry=registry,
        tools=default_registry(),
        act_tools=docker_act_tools(SECRET, guard_log),
        models=lambda _role: world.model,
        bus=events,
        ledger=CostLedger(db),
        incidents=repo,
        action_log=action_log,
        approvals=approvals,
        guard_log=guard_log,
        settings_store=StaticSettings(budget_limits=world.budgets),
        sleep=sleep,
        http=http,
    )
    try:
        async with open_checkpointer(world.tmp / "checkpoints.sqlite") as saver:
            runner = IncidentRunner(build_graph(deps, saver), repo, events, approvals)
            try:
                yield Bleep(repo, events, approvals, action_log, runner)
            finally:
                await runner.close()  # whatever is in flight stops here, mid-node
    finally:
        await http.aclose()


def plan(*, urls: tuple[str, ...] = ()) -> AIMessage:
    step = {"action": "docker.restart", "host_id": "a", "target": "shop-web-1"}
    verification = {"containers": ["shop-web-1"], "urls": list(urls)}
    body = {"kind": "act", "steps": [step], "reason": "it exited", "verification": verification}
    return AIMessage(json.dumps(body))


def one_round(proposal: AIMessage) -> list[AIMessage]:
    """Investigate reads nothing more, Diagnose is confident, Propose restarts shop-web-1."""
    return [AIMessage("nothing more to read"), AIMessage(json.dumps(DIAGNOSIS)), proposal]


def world(tmp_path: Path, script: list[AIMessage], **fields) -> World:
    clock = Ticks()
    host = Host(
        "a",
        [
            box("shop-web-1", "shop", state="exited", exit_code=1, restart_policy="no"),
            box("shop-db-1", "shop", started_at=T0),
        ],
        clock=clock,
    )
    model = FakeChatModel(script=script, structured_output_unsupported=True)
    return World(tmp_path, host, model, clock, **fields)


async def asking(proc: Bleep) -> Incident:
    """Open the shop's incident and run it to the approval question."""
    subject = Subject(kind="service", host_id="a", service_id="a/shop")
    incident, _ = await proc.repo.open_or_attach(
        subject, [Signal(subject=subject, kind="container_died", at=T0, container="shop-web-1")]
    )
    await proc.runner.start(incident)
    await proc.runner.join()
    assert await proc.runner.waiting_for(incident.id) is not None
    assert await proc.status(incident.id) == "awaiting_approval"
    return incident


async def reached(crash: Crash) -> None:
    await asyncio.wait_for(crash.reached.wait(), 5)


# Killed while waiting for approval


async def test_killed_while_waiting_for_approval_the_decision_on_a_new_process_executes_once(
    tmp_path,
):
    w = world(tmp_path, [*one_round(plan()), AIMessage(json.dumps(REPORT))])
    async with bleep(w) as first:
        incident = await asking(first)  # then the process exits while the question is open

    async with bleep(w) as second:
        assert await second.runner.recover_on_boot() == []  # still waiting for the human
        await second.approve(incident.id)
        await second.runner.join()

        assert w.host.restarts() == ["shop-web-1"]
        assert (await second.values(incident.id))["outcome"] == "resolved"
        assert await second.status(incident.id) == "resolved"

    async with bleep(w) as third:  # a finished incident is left alone by later boots
        assert await third.runner.recover_on_boot() == []
        await third.runner.start(incident)
        await third.runner.join()
    assert w.host.restarts() == ["shop-web-1"]


async def test_a_decision_recorded_just_before_the_process_died_is_delivered_on_boot(tmp_path):
    """The dashboard stored the yes, and Bleep died before the resumed run reached a checkpoint
    (#20, #72): the thread is still at the interrupt and the human must not be asked again."""
    w = world(tmp_path, [*one_round(plan()), AIMessage(json.dumps(REPORT))])
    async with bleep(w) as first:
        incident = await asking(first)
        approval_id = await first.runner.waiting_for(incident.id)
        decision = Decision(kind="approve", via="telegram", at=T0)
        assert await first.approvals.resolve(incident.id, decision, approval_id) == decision
        # died before the resume reached the runner
    async with bleep(w) as second:
        assert await second.status(incident.id) == "awaiting_approval"
        assert await second.runner.recover_on_boot() == [incident.id]
        await second.runner.join()

        assert w.host.restarts() == ["shop-web-1"]
        assert (await second.values(incident.id))["outcome"] == "resolved"
        assert await second.status(incident.id) == "resolved"
        assert len(await second.events(incident.id, "approval_requested")) == 1


async def test_a_decision_whose_resumed_run_died_in_the_approval_node_executes_once(tmp_path):
    w = world(tmp_path, [*one_round(plan()), AIMessage(json.dumps(REPORT))])
    async with bleep(w) as first:
        incident = await asking(first)
    crash = Crash()
    async with bleep(w, bus=lambda db: DyingBus(db, "decision", crash)) as second:
        await second.approve(incident.id)
        await reached(crash)  # the approval node is running again, the decision not yet out
        assert await second.status(incident.id) == "investigating"

    async with bleep(w) as third:
        assert await third.runner.recover_on_boot() == [incident.id]
        await third.runner.join()

        assert w.host.restarts() == ["shop-web-1"]
        assert (await third.values(incident.id))["outcome"] == "resolved"
        assert len(await third.events(incident.id, "decision")) == 1
        assert len(await third.events(incident.id, "approval_requested")) == 1


# Killed between an action's start and its done


async def test_killed_after_the_restart_reached_the_engine_the_new_process_restarts_nothing(
    tmp_path,
):
    w = world(tmp_path, [*one_round(plan()), AIMessage(json.dumps(REPORT))])
    async with bleep(w) as first:
        incident = await asking(first)
    crash = w.host.after_restart = Crash()
    async with bleep(w) as second:
        await second.approve(incident.id)
        await reached(crash)  # the container restarted; `done` was never written
        assert [s.status for s in await second.action_log.steps(incident.id)] == ["started"]
    assert w.host.restarts() == ["shop-web-1"]

    async with bleep(w) as third:
        assert await third.status(incident.id) == "investigating"
        assert await third.runner.recover_on_boot() == [incident.id]
        await third.runner.join()

        assert w.host.restarts() == ["shop-web-1"]  # one engine call in total
        state = await third.values(incident.id)
        assert [r.detail for r in state["step_results"]] == [
            "restarted shop-web-1, confirmed after a restart"
        ]
        assert state["outcome"] == "resolved"
        assert await third.status(incident.id) == "resolved"
        assert [s.status for s in await third.action_log.steps(incident.id)] == ["done"]
        assert [e["status"] for e in await third.events(incident.id, "action")] == [
            "started",
            "done",
        ]
        assert (await third.nodes(incident.id)).count("execute") == 2  # ran again, acted once


async def test_killed_after_the_step_was_logged_done_execute_reruns_and_acts_on_nothing(tmp_path):
    """Bleep died after `done` was written and before Execute's checkpoint: the node runs again
    and keeps the logged result."""
    w = world(tmp_path, [*one_round(plan()), AIMessage(json.dumps(REPORT))])
    async with bleep(w) as first:
        incident = await asking(first)
    crash = Crash()
    async with bleep(w, bus=lambda db: DyingBus(db, "action", crash, status="done")) as second:
        await second.approve(incident.id)
        await reached(crash)
        assert [s.status for s in await second.action_log.steps(incident.id)] == ["done"]

    async with bleep(w) as third:
        assert await third.runner.recover_on_boot() == [incident.id]
        await third.runner.join()

        assert w.host.restarts() == ["shop-web-1"]
        assert (await third.nodes(incident.id)).count("execute") == 2
        state = await third.values(incident.id)
        assert [r.detail for r in state["step_results"]] == ["restarted shop-web-1"]
        assert state["outcome"] == "resolved"


async def test_killed_after_started_before_the_engine_call_the_step_is_never_run(tmp_path):
    """The other side of the same gap: `started` was written and the call never left. The step
    may have run, so it is not run again; it is reported failed and Verify judges the service."""
    w = world(
        tmp_path,
        [*one_round(plan()), AIMessage(json.dumps(REPORT))],
        budgets=Budgets(verify_loops=0),
    )
    async with bleep(w) as first:
        incident = await asking(first)
    crash = w.host.before_restart = Crash()
    async with bleep(w) as second:
        await second.approve(incident.id)
        await reached(crash)

    async with bleep(w) as third:
        assert await third.runner.recover_on_boot() == [incident.id]
        await third.runner.join()

        assert w.host.restarts() == []
        state = await third.values(incident.id)
        assert not state["step_results"][0].ok
        assert "not run again" in state["step_results"][0].detail
        assert state["outcome"] == "unverified"
        assert await third.status(incident.id) == "closed"


# Killed during Verify


async def test_killed_during_verify_verify_runs_again_and_nothing_acts_twice(tmp_path):
    w = world(tmp_path, [*one_round(plan()), AIMessage(json.dumps(REPORT))])
    async with bleep(w) as first:
        incident = await asking(first)
    crash = Crash()

    async def dying_wait(_seconds: float) -> None:
        await crash.here()

    async with bleep(w, sleep=dying_wait) as second:
        await second.approve(incident.id)
        await reached(crash)  # Verify is waiting for the service to come up
        assert (await second.nodes(incident.id))[-1] == "verify"

    async with bleep(w) as third:
        assert await third.runner.recover_on_boot() == [incident.id]
        await third.runner.join()

        assert w.host.restarts() == ["shop-web-1"]
        nodes = await third.nodes(incident.id)
        assert nodes.count("verify") == 2 and nodes.count("execute") == 1
        assert await third.nodes(incident.id, "node_finished") == [
            "gather", "investigate", "diagnose", "propose", "approval", "execute", "verify",
            "report",
        ]  # fmt: skip
        assert (await third.values(incident.id))["outcome"] == "resolved"
        assert await third.status(incident.id) == "resolved"


# The same plan approved again after Verify's loop-back


async def test_the_same_plan_approved_again_runs_once_more_and_a_crash_never_runs_it_twice(
    tmp_path,
):
    """The first restart does not bring the URL back; Verify sends the run back, the same
    restart is proposed and approved again, and Bleep dies right after the second restart."""
    script = [
        *one_round(plan(urls=(URL,))),
        *one_round(plan(urls=(URL,))),
        AIMessage(json.dumps(REPORT)),
    ]
    config = shop_host(services={"shop": ServiceOverrides(urls=[UrlCheckConfig(url=URL)])})
    w = world(tmp_path, script, config=config, budgets=Budgets(verify_loops=1))
    w.answering = lambda: len(w.host.restarts()) >= 2

    async with bleep(w) as first:
        incident = await asking(first)
        first_id = await first.approve(incident.id)
        await first.runner.join()  # restarted, verified three times, failed, asked again

        second_id = await first.runner.waiting_for(incident.id)
        assert second_id is not None and second_id != first_id
        asked = await first.events(incident.id, "approval_requested")
        assert [a["approval_id"] for a in asked] == [first_id, second_id]
        assert asked[0]["plan_hash"] == asked[1]["plan_hash"]  # the very same plan
        assert w.host.restarts() == ["shop-web-1"]

    crash = w.host.after_restart = Crash()
    async with bleep(w) as second:
        assert await second.runner.recover_on_boot() == []  # the second question is open
        await second.approve(incident.id)
        await reached(crash)
    assert w.host.restarts() == ["shop-web-1", "shop-web-1"]

    async with bleep(w) as third:
        assert await third.runner.recover_on_boot() == [incident.id]
        await third.runner.join()
        # Late answers to either question change nothing.
        late = Decision(kind="approve", via="telegram", at=T0)
        for approval_id in (first_id, second_id):
            await third.runner.resume(incident.id, late, approval_id)
        await third.runner.join()

        assert w.host.restarts() == ["shop-web-1", "shop-web-1"]
        state = await third.values(incident.id)
        assert [r.ok for r in state["step_results"]] == [True, True]
        assert state["outcome"] == "resolved"
        assert await third.status(incident.id) == "resolved"
        logged = await third.action_log.steps(incident.id)
        assert [(s.plan_key, s.status) for s in logged] == [
            (f"{asked[0]['plan_hash']}:{first_id}", "done"),
            (f"{asked[0]['plan_hash']}:{second_id}", "done"),
        ]


# Killed at the end


async def test_killed_after_the_report_before_its_checkpoint_the_run_is_finished_on_boot(tmp_path):
    """The report went out, and Bleep died before the run's last checkpoint. The incident must
    not be left closed with no report where the API reads it (the checkpoint): it stays open,
    and the next boot writes the report again and closes it."""
    w = world(tmp_path, [*one_round(plan()), *[AIMessage(json.dumps(REPORT))] * 2])
    async with bleep(w) as first:
        incident = await asking(first)
    crash = Crash()
    async with bleep(
        w, bus=lambda db: DyingBus(db, "node_finished", crash, node="report")
    ) as second:
        await second.approve(incident.id)
        await reached(crash)
        assert len(await second.events(incident.id, "report")) == 1

    async with bleep(w) as third:
        assert await third.runner.recover_on_boot() == [incident.id]
        await third.runner.join()

        assert w.host.restarts() == ["shop-web-1"]
        state = await third.values(incident.id)
        assert state["outcome"] == "resolved"
        assert state["report"].outcome == "Fixed and verified"
        assert await third.status(incident.id) == "resolved"
