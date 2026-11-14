import asyncio
from datetime import UTC, datetime, timedelta

import anthropic
import httpx2
import pytest
from langchain_core.callbacks import BaseCallbackHandler

from bleep import prompts
from bleep.graph.build import build_graph, open_checkpointer
from bleep.graph.deps import StaticSettings
from bleep.graph.nodes import propose, verify
from bleep.graph.shown import english
from bleep.incidents import runner as notices_of
from bleep.incidents.approvals import ApprovalStore
from bleep.incidents.bus import EventBus
from bleep.incidents.runner import IncidentRunner
from bleep.incidents.watchdog import Watchdog
from bleep.schemas.graph import (
    Decision,
    PlanStep,
    Proposal,
    VerificationPlan,
    VerificationResult,
)
from bleep.schemas.incidents import Signal, Subject
from bleep.schemas.settings import Budgets
from bleep.schemas.shown import Shown
from fakes import FakeChatModel
from graph_env import T0, confident_model, graph_env, no_wait

M2_PATH = ["gather", "investigate", "diagnose", "propose", "report"]
# The notices as they are stored and as the channels were handed them in English.
ANSWERED_NOTICE = english(notices_of.ANSWERED_NOTICE)
BACK_NOTICE = english(notices_of.BACK_NOTICE)
QUIET_RUN_NOTICE = english(notices_of.QUIET_RUN_NOTICE)
QUIET_RUN_NOTICE_HOST = english(notices_of.QUIET_RUN_NOTICE_HOST)
WAITING_NOTICE = english(notices_of.WAITING_NOTICE)
REMOVED = Shown.of("notice/removed")
SNOOZED = Shown.of("notice/snoozed", name="shop")
LEFT_ALONE = Shown.of("notice/left_alone", name="shop")


def service(env) -> Subject:
    return Subject(kind="service", host_id="a", service_id=env.service_id("shop"))


def decide(kind: str, text: str | None = None) -> Decision:
    return Decision(kind=kind, text=text, via="dashboard", at=datetime(2026, 11, 2, tzinfo=UTC))


@pytest.fixture
def acting(monkeypatch):
    """Make Propose return a one-step restart, so the run reaches Approval."""

    def make(deps):
        async def node(state):
            return {
                "proposal": Proposal(
                    kind="act",
                    steps=[PlanStep(action="docker.restart", host_id="a", target="shop-web-1")],
                    reason="it exited",
                    verification=VerificationPlan(containers=["shop-web-1"]),
                )
            }

        return node

    monkeypatch.setattr(propose, "make", make)


async def values(runner: IncidentRunner, incident_id: str) -> dict:
    return (await runner.graph.aget_state(runner.config(incident_id))).values


async def test_start_runs_every_node_in_order_and_closes(tmp_path):
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus)
        await runner.start(incident)
        await runner.join()

        assert await env.nodes(incident.id) == M2_PATH
        assert await env.nodes(incident.id, "node_finished") == M2_PATH
        state = await values(runner, incident.id)
        assert [s.container for s in state["signals"]] == ["shop-web-1"]
        assert state["context"].service_name == "shop"
        assert state["outcome"] == "no_action"
    assert (await env.repo.get(incident.id)).status == "closed"


class BlockingBus(EventBus):
    """Hangs forever when a node starts, as if Bleep died at that moment."""

    def __init__(self, db, node: str) -> None:
        super().__init__(db)
        self.node = node
        self.reached = asyncio.Event()

    async def publish(self, incident_id, kind, payload=None):
        if kind == "node_started" and (payload or {}).get("node") == self.node:
            self.reached.set()
            await asyncio.Event().wait()
        return await super().publish(incident_id, kind, payload)


async def test_interrupted_run_resumes_from_the_checkpoint_on_a_new_runner(tmp_path):
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")
    checkpoints = tmp_path / "checkpoints.sqlite"

    env.deps.bus = BlockingBus(env.db, "diagnose")
    async with open_checkpointer(checkpoints) as saver:
        first = IncidentRunner(build_graph(env.deps, saver), env.repo, env.deps.bus)
        await first.start(incident)
        await asyncio.wait_for(env.deps.bus.reached.wait(), 5)
        await first.close()  # the process dies mid-graph
    assert (await env.repo.get(incident.id)).status == "investigating"
    assert await env.nodes(incident.id) == ["gather", "investigate"]

    env.deps.bus = env.bus
    async with open_checkpointer(checkpoints) as saver:
        second = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus)
        assert await second.recover_on_boot() == [incident.id]
        await second.join()

    # Gather and Investigate ran once: the new runner went on from the last checkpoint.
    assert await env.nodes(incident.id) == M2_PATH
    assert (await env.repo.get(incident.id)).status == "closed"


async def test_recover_on_boot_skips_awaiting_approval_and_finished_runs(tmp_path):
    env = await graph_env(tmp_path)
    waiting = await env.open(service(env), container="shop-web-1")
    fresh = await env.open(Subject(kind="host", host_id="a"), kind="disk")
    done = await env.open(Subject(kind="service", host_id="a", service_id="a/gone"))
    await env.repo.set_status(waiting.id, "awaiting_approval")
    await env.repo.set_status(done.id, "closed")

    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus)
        assert await runner.recover_on_boot() == [fresh.id]
        await runner.join()

    assert await env.nodes(waiting.id) == []
    assert await env.nodes(done.id) == []
    assert await env.nodes(fresh.id) == M2_PATH  # never started before the restart: starts now


async def test_waits_for_approval_and_resumes_on_a_decision_after_a_restart(tmp_path, acting):
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")
    checkpoints = tmp_path / "checkpoints.sqlite"
    async with open_checkpointer(checkpoints) as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus)
        await runner.start(incident)
        await runner.join()
    assert (await env.repo.get(incident.id)).status == "awaiting_approval"
    assert (await env.nodes(incident.id))[-1] == "approval"

    async with open_checkpointer(checkpoints) as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus)
        assert await runner.recover_on_boot() == []  # still waiting for the human
        await runner.resume(incident.id, decide("reject"), await runner.waiting_for(incident.id))
        await runner.join()
        state = await values(runner, incident.id)

    assert state["decision"].kind == "reject"
    assert state["outcome"] == "rejected"
    assert (await env.repo.get(incident.id)).status == "closed"
    assert (await env.nodes(incident.id, "node_finished"))[-2:] == ["approval", "report"]


async def test_a_run_records_the_overrides_it_started_with_and_a_resume_keeps_them(
    tmp_path, acting
):
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")
    plain = await env.open(Subject(kind="host", host_id="a"), kind="disk")
    checkpoints = tmp_path / "checkpoints.sqlite"
    assert (await env.repo.get(incident.id)).prompt_overrides is None
    async with open_checkpointer(checkpoints) as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus)
        await runner.start(plain)
        await runner.join()
        prompts.use_overrides({"watcher/recovery_line": "First: {logs}"})
        await runner.start(incident)
        await runner.join()
    started = {"watcher/recovery_line": prompts.text_version("First: {logs}")}
    assert (await env.repo.get(incident.id)).prompt_overrides == started
    assert (await env.repo.get(plain.id)).prompt_overrides == {}

    prompts.use_overrides({"watcher/recovery_line": "Second: {logs}", "graph/propose_unsure": "x"})
    async with open_checkpointer(checkpoints) as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus)
        await runner.resume(incident.id, decide("reject"), await runner.waiting_for(incident.id))
        await runner.join()
    assert (await env.repo.get(incident.id)).status == "closed"
    assert (await env.repo.get(incident.id)).prompt_overrides == started


async def test_a_decision_for_another_approval_does_not_resume_the_run(tmp_path, acting):
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus)
        await runner.start(incident)
        await runner.join()
        waiting = await runner.waiting_for(incident.id)
        assert waiting is not None

        await runner.resume(incident.id, decide("approve"), "an-earlier-plan")
        await runner.join()

        assert await runner.waiting_for(incident.id) == waiting  # still asking the same question
        assert "decision" not in await values(runner, incident.id)
    assert (await env.repo.get(incident.id)).status == "awaiting_approval"


class OpenedNotifier:
    def __init__(self, fail: bool = False) -> None:
        self.opened: list[str] = []
        self.fail = fail

    async def incident_opened(self, incident) -> None:
        self.opened.append(incident.id)
        if self.fail:
            raise RuntimeError("telegram is down")


async def test_a_new_run_tells_the_notifier_the_incident_opened_once(tmp_path):
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")
    notifier = OpenedNotifier()
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, notifier=notifier)
        await runner.start(incident)
        await runner.join()
        await runner.start(incident)  # finished: nothing new to tell
        await runner.join()

    assert notifier.opened == [incident.id]  # no_action, unsure and budget incidents included
    assert await env.nodes(incident.id) == M2_PATH


async def test_a_failing_notifier_does_not_stop_the_run(tmp_path):
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")
    notifier = OpenedNotifier(fail=True)
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, notifier=notifier)
        await runner.start(incident)
        await runner.join()

    assert notifier.opened == [incident.id]
    assert await env.nodes(incident.id) == M2_PATH
    assert (await env.repo.get(incident.id)).status == "closed"


async def test_a_run_going_on_after_a_restart_does_not_announce_the_incident_again(
    tmp_path, acting
):
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")
    notifier = OpenedNotifier()
    checkpoints = tmp_path / "checkpoints.sqlite"
    async with open_checkpointer(checkpoints) as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, notifier=notifier)
        await runner.start(incident)
        await runner.join()
    async with open_checkpointer(checkpoints) as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, notifier=notifier)
        await runner.start(incident)  # waiting at approval: picked up, not opened again
        await runner.join()

    assert notifier.opened == [incident.id]


async def test_state_is_the_checkpointed_values_and_empty_before_a_run(tmp_path, acting):
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus)
        assert await runner.state(incident.id) == {}
        await runner.start(incident)
        await runner.join()
        state = await runner.state(incident.id)
    assert state["diagnosis"].root_cause == "the container exited"
    assert state["proposal"].steps[0].target == "shop-web-1"


async def test_start_twice_does_not_run_the_graph_twice(tmp_path):
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus)
        await runner.start(incident)
        await runner.start(incident)  # while the first run is in flight
        await runner.join()
        await runner.start(incident)  # after it finished
        await runner.join()

    assert await env.nodes(incident.id) == M2_PATH


async def test_a_failing_node_marks_the_incident_failed(tmp_path, monkeypatch):
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")

    def make(deps):
        async def node(state):
            raise RuntimeError("boom")

        return node

    monkeypatch.setattr(propose, "make", make)
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus)
        await runner.start(incident)
        await runner.join()

    assert (await env.repo.get(incident.id)).status == "failed"
    notices = [e for e in await env.bus.history(incident.id) if e.kind == "notice"]
    assert notices and "RuntimeError" in notices[-1].payload["text"]


class NoticeNotifier:
    """Records notices; with `fail`, every send raises as a channel outage would."""

    def __init__(self, fail: bool = False) -> None:
        self.notices: list[tuple[str, str]] = []
        self.endings: list[str | None] = []
        self.fail = fail

    async def incident_opened(self, incident) -> None:
        return None

    async def notice(self, incident_id: str, text: Shown, ending: str | None = None) -> None:
        if self.fail:
            raise RuntimeError("telegram is down")
        self.notices.append((incident_id, english(text)))
        self.endings.append(ending)


@pytest.mark.parametrize("fail", [False, True])
async def test_a_failing_run_tells_the_channels_too(tmp_path, monkeypatch, fail, caplog):
    """M10: after an approval the human waits on Telegram; a run that dies must say so there."""
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")

    def make(deps):
        async def node(state):
            raise RuntimeError("boom")

        return node

    monkeypatch.setattr(propose, "make", make)
    notifier = NoticeNotifier(fail=fail)
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, notifier=notifier)
        task = await runner.start(incident)
        await runner.join()

    assert task.exception() is None
    assert (await env.repo.get(incident.id)).status == "failed"
    [notice] = [e.payload["text"] for e in await env.bus.history(incident.id) if e.kind == "notice"]
    if fail:
        assert "telling the channels" in caplog.text
    else:
        assert notifier.notices == [(incident.id, notice)] and "RuntimeError" in notice
        assert notifier.endings == ["error"]


async def test_a_status_write_that_fails_is_logged_and_fails_the_run(tmp_path, caplog):
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")
    set_status = env.repo.set_status

    async def flaky(incident_id: str, status: str) -> None:
        if status == "investigating":
            raise RuntimeError("database is locked")
        await set_status(incident_id, status)

    env.repo.set_status = flaky  # type: ignore[method-assign]
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus)
        task = await runner.start(incident)
        await runner.join()

    assert task.exception() is None  # nothing left for shutdown to find
    assert (await env.repo.get(incident.id)).status == "failed"
    notices = [e for e in await env.bus.history(incident.id) if e.kind == "notice"]
    assert notices and "RuntimeError" in notices[-1].payload["text"]
    assert any(r.levelname == "ERROR" and incident.id in r.getMessage() for r in caplog.records)


async def test_a_run_whose_every_status_write_fails_ends_logged_not_raising(tmp_path, caplog):
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")

    async def broken(incident_id: str, status: str) -> None:
        raise RuntimeError("disk full")

    env.repo.set_status = broken  # type: ignore[method-assign]
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus)
        task = await runner.start(incident)
        await runner.join()

    assert task.exception() is None
    assert sum(r.levelname == "ERROR" for r in caplog.records) >= 2  # the run and the status


async def test_a_decision_for_a_run_that_is_not_waiting_changes_nothing(tmp_path):
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus)
        await runner.resume(incident.id, decide("approve"), "a1")  # never started
        await runner.join()
        assert await env.nodes(incident.id) == []
        assert (await env.repo.get(incident.id)).status == "open"

        await runner.start(incident)
        await runner.join()
        await runner.resume(incident.id, decide("approve"), "a1")  # already finished
        await runner.join()

    assert await env.nodes(incident.id) == M2_PATH
    assert (await env.repo.get(incident.id)).status == "closed"


async def test_recover_on_boot_closes_a_finished_thread_whose_status_never_settled(tmp_path):
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus)
        await runner.start(incident)
        await runner.join()
        # Bleep died after the last checkpoint but before the status was written.
        await env.repo.set_status(incident.id, "investigating")

        second = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus)
        await second.recover_on_boot()
        await second.join()

    assert (await env.repo.get(incident.id)).status == "closed"


@pytest.mark.parametrize("status", ["awaiting_approval", "investigating"])
async def test_a_decision_recorded_before_a_crash_is_delivered_on_boot(tmp_path, acting, status):
    """Bleep died after the approval was decided but before the resumed run was checkpointed:
    the incident is still `awaiting_approval` (died before `resume`) or `investigating` (died
    inside it), and the thread is still at the interrupt."""
    env = await graph_env(tmp_path)
    store = ApprovalStore(env.db, "test-secret")
    env.deps.approvals = store
    incident = await env.open(service(env), container="shop-web-1")
    checkpoints = tmp_path / "checkpoints.sqlite"
    async with open_checkpointer(checkpoints) as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, store)
        await runner.start(incident)
        await runner.join()
    assert await store.resolve(incident.id, decide("reject")) is not None
    await env.repo.set_status(incident.id, status)

    async with open_checkpointer(checkpoints) as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, store)
        assert await runner.recover_on_boot() == [incident.id]
        await runner.join()
        state = await values(runner, incident.id)

    assert state["decision"].kind == "reject"
    assert state["outcome"] == "rejected"
    assert (await env.repo.get(incident.id)).status == "closed"


async def test_recover_on_boot_leaves_an_undecided_approval_waiting(tmp_path, acting):
    env = await graph_env(tmp_path)
    store = ApprovalStore(env.db, "test-secret")
    env.deps.approvals = store
    incident = await env.open(service(env), container="shop-web-1")
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, store)
        await runner.start(incident)
        await runner.join()
        await env.repo.set_status(incident.id, "investigating")  # died before the status settled

        second = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, store)
        assert await second.recover_on_boot() == [incident.id]
        await second.join()

    assert (await env.repo.get(incident.id)).status == "awaiting_approval"
    assert (await store.current(incident.id)).decision is None
    assert (
        len([e for e in await env.bus.history(incident.id) if e.kind == "approval_requested"]) == 1
    )


class RootRuns(BaseCallbackHandler):
    """Records what a tracer sees: each graph invocation's root run, and the session of every
    model and tool call under them."""

    def __init__(self) -> None:
        self.roots: list[dict] = []
        self.calls: list[tuple[str, str | None, str | None]] = []

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kwargs):
        if parent_run_id is None:
            self.roots.append(
                {"name": kwargs.get("name"), "tags": kwargs.get("tags"), **kwargs["metadata"]}
            )

    def on_chat_model_start(self, serialized, messages, *, run_id, metadata=None, **kwargs):
        self._call("model", metadata or {})

    def on_tool_start(self, serialized, input_str, *, run_id, metadata=None, **kwargs):
        self._call("tool", metadata or {})

    def _call(self, kind: str, metadata: dict) -> None:
        self.calls.append((kind, metadata.get("session"), metadata.get("langgraph_node")))


class RecordingTracer:
    def __init__(self) -> None:
        self.seen = RootRuns()

    def run_config(self, incident):
        return {
            "callbacks": [self.seen],
            "run_name": f"incident:{incident.title}",
            "tags": ["host:a"],
            "metadata": {"session": incident.id},
        }

    def trace_url(self, incident):
        return f"http://traces.test/{incident.id}"

    async def close(self):
        return None


async def test_every_run_of_an_incident_carries_its_id_as_the_trace_session(tmp_path, acting):
    """The first run stops at Approval; the resume is a second graph invocation. Both are traced
    with the incident as their session, and the link is stored on the incident."""
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")
    tracer = RecordingTracer()
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, tracer=tracer)
        await runner.start(incident)
        await runner.join()
        await runner.resume(incident.id, decide("reject"), await runner.waiting_for(incident.id))
        await runner.join()

    assert [root["session"] for root in tracer.seen.roots] == [incident.id, incident.id]
    assert {root["name"] for root in tracer.seen.roots} == {f"incident:{incident.title}"}
    assert all(root["thread_id"] == incident.id for root in tracer.seen.roots)
    # The callbacks reach the model call of every node that makes one (Propose is stubbed here),
    # beside the cost tracker each node passes: not only the roots.
    model_nodes = {node for kind, _, node in tracer.seen.calls if kind == "model"}
    assert model_nodes == {"investigate", "diagnose", "report"}
    assert {session for _, session, _ in tracer.seen.calls} == {incident.id}
    assert (await env.repo.get(incident.id)).trace_url == f"http://traces.test/{incident.id}"


async def test_without_a_tracer_a_run_gets_no_callbacks_and_no_link(tmp_path):
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus)
        assert runner.run_config(incident) == {"configurable": {"thread_id": incident.id}}
        await runner.start(incident)
        await runner.join()

    assert (await env.repo.get(incident.id)).trace_url is None


class BrokenTracer(RecordingTracer):
    def run_config(self, incident):
        raise RuntimeError("tracing backend misconfigured")


async def test_a_failing_tracer_never_stops_the_run(tmp_path):
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(
            build_graph(env.deps, saver), env.repo, env.bus, tracer=BrokenTracer()
        )
        await runner.start(incident)
        await runner.join()

    assert await env.nodes(incident.id) == M2_PATH
    assert (await env.repo.get(incident.id)).status == "closed"


class HangingModel(FakeChatModel):
    """Hangs on its first `hangs` calls, then answers from the script."""

    hangs: int = 0

    async def _agenerate(self, *args, **kwargs):
        if self.hangs > 0:
            self.hangs -= 1
            await asyncio.Event().wait()
        return await super()._agenerate(*args, **kwargs)


class FailingModel(FakeChatModel):
    """Raises `failure` on its first `failures` calls, then answers from the script."""

    failure: Exception
    failures: int = 0

    async def _agenerate(self, *args, **kwargs):
        if self.failures > 0:
            self.failures -= 1
            raise self.failure
        return await super()._agenerate(*args, **kwargs)


class WallClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class Retry:
    """The runner's `sleep`: records each wait, moves `clock` on by it, and holds the retry until
    the test lets it go."""

    def __init__(self, clock: WallClock | None = None) -> None:
        self.clock = clock or WallClock()
        self.delays: list[float] = []
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)
        self.waiting.set()
        await self.release.wait()
        self.clock.now += timedelta(seconds=seconds)
        self.release.clear()
        self.waiting.clear()


async def model_down(tmp_path, hangs: int):
    """An environment whose model hangs for `hangs` calls, cut off after 10 ms with no waiting."""
    env = await graph_env(tmp_path)
    script = confident_model().script
    env.models.model = HangingModel(script=script, structured_output_unsupported=True, hangs=hangs)
    env.deps.settings_store = StaticSettings(
        budget_limits=Budgets.model_construct(
            **(Budgets().model_dump() | {"model_timeout_s": 0.01})
        )
    )
    env.deps.model_sleep = no_wait
    return env


async def paused(runner: IncidentRunner, retry: Retry) -> None:
    await asyncio.wait_for(retry.waiting.wait(), 5)


async def test_a_model_that_stays_unavailable_pauses_the_run_instead_of_failing_it(tmp_path):
    env = await model_down(tmp_path, hangs=3)  # the call and its two retries
    incident = await env.open(service(env), container="shop-web-1")
    notifier, retry = NoticeNotifier(), Retry()
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(
            build_graph(env.deps, saver),
            env.repo,
            env.bus,
            notifier=notifier,
            settings=env.deps.settings_store,
            sleep=retry,
        )
        await runner.start(incident)
        await paused(runner, retry)

        assert (await env.repo.get(incident.id)).status == "paused"
        assert retry.delays == [300]  # five minutes by default
        notices = [
            e.payload["text"] for e in await env.bus.history(incident.id) if e.kind == "notice"
        ]
        assert len(notices) == 1 and "paused" in notices[0] and "TimeoutError" in notices[0]
        assert notifier.notices == [(incident.id, notices[0])]
        await runner.close()


@pytest.mark.parametrize("status", [429, 500, 529])
async def test_a_claude_that_stays_down_pauses_the_run_instead_of_failing_it(tmp_path, status):
    """The Anthropic API's outage errors (rate limit, 5xx, 529 overloaded) retry and then pause
    the incident, as a hanging model does (#504)."""
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    error = {
        429: anthropic.RateLimitError,
        500: anthropic.InternalServerError,
        529: anthropic.OverloadedError,
    }[status]("down", response=httpx2.Response(status, request=request), body=None)
    env = await graph_env(tmp_path)
    env.models.model = FailingModel(
        script=confident_model().script,
        structured_output_unsupported=True,
        failure=error,
        failures=3,  # the call and its two retries
    )
    env.deps.model_sleep = no_wait
    incident = await env.open(service(env), container="shop-web-1")
    notifier, retry = NoticeNotifier(), Retry()
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(
            build_graph(env.deps, saver),
            env.repo,
            env.bus,
            notifier=notifier,
            settings=env.deps.settings_store,
            sleep=retry,
        )
        await runner.start(incident)
        await paused(runner, retry)

        assert (await env.repo.get(incident.id)).status == "paused"
        assert retry.delays == [300]
        assert len(notifier.notices) == 1 and type(error).__name__ in notifier.notices[0][1]
        await runner.close()


async def test_a_paused_run_goes_on_from_its_checkpoint_and_finishes(tmp_path):
    env = await model_down(tmp_path, hangs=3)
    incident = await env.open(service(env), container="shop-web-1")
    notifier, retry = NoticeNotifier(), Retry()
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(
            build_graph(env.deps, saver),
            env.repo,
            env.bus,
            notifier=notifier,
            settings=env.deps.settings_store,
            sleep=retry,
        )
        await runner.start(incident)
        await paused(runner, retry)
        retry.release.set()
        await runner.join()

        assert (await env.repo.get(incident.id)).status == "closed"
        started = await env.nodes(incident.id)
        assert started.count("gather") == 1  # the checkpoint, not a new run
        assert started[-1] == "report"
        assert len(notifier.notices) == 1


async def test_a_run_paused_twice_in_a_row_tells_the_channels_once(tmp_path):
    env = await model_down(tmp_path, hangs=6)
    incident = await env.open(service(env), container="shop-web-1")
    notifier, retry = NoticeNotifier(), Retry()
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(
            build_graph(env.deps, saver),
            env.repo,
            env.bus,
            notifier=notifier,
            settings=env.deps.settings_store,
            sleep=retry,
        )
        await runner.start(incident)
        await paused(runner, retry)
        retry.release.set()
        await asyncio.wait_for(retry.waiting.wait(), 5)  # paused again, after the second attempt
        assert (await env.repo.get(incident.id)).status == "paused"
        retry.release.set()
        await runner.join()

        assert (await env.repo.get(incident.id)).status == "closed"
        assert len(notifier.notices) == 1
        assert retry.delays == [300, 600]


async def test_recover_on_boot_picks_up_a_paused_run(tmp_path):
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")
    await env.repo.set_status(incident.id, "paused")
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus)
        assert await runner.recover_on_boot() == [incident.id]
        await runner.join()
    assert (await env.repo.get(incident.id)).status == "closed"


async def test_a_long_outage_gives_the_pause_notice_and_one_at_an_hour_and_the_watchdog_none(
    tmp_path,
):
    env = await model_down(tmp_path, hangs=3 * 7)  # seven pauses
    incident = await env.open(service(env), container="shop-web-1")
    notifier, retry = NoticeNotifier(), Retry()
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(
            build_graph(env.deps, saver),
            env.repo,
            env.bus,
            notifier=notifier,
            settings=env.deps.settings_store,
            sleep=retry,
            clock=retry.clock,
        )
        dog = Watchdog(
            env.repo, env.bus, notifier, env.deps.settings_store, retry.clock, skip=runner.retrying
        )
        await runner.start(incident)
        for _ in range(6):
            await paused(runner, retry)
            assert await dog.tick(retry.clock.now + timedelta(hours=3)) == []
            await env.repo.set_status(incident.id, "investigating")  # a retry in progress
            assert await dog.tick(retry.clock.now + timedelta(hours=3)) == []
            await env.repo.set_status(incident.id, "paused")
            retry.release.set()
            await asyncio.sleep(0.05)
        await paused(runner, retry)
        assert retry.delays == [300, 600, 1200, 2400, 3600, 3600, 3600]
        # The pause notice, and one at the first pause after an hour (5+10+20+40 min is 75).
        assert len(notifier.notices) == 2
        assert "an hour" in notifier.notices[1][1]
        notices = [e for e in await env.bus.history(incident.id) if e.kind == "notice"]
        assert len(notices) == 2
        started = await env.nodes(incident.id)
        assert started.count("investigate") == 1  # retries do not repeat it on the timeline
        await runner.close()


async def test_the_retry_ceiling_is_never_below_the_configured_first_wait(tmp_path):
    env = await model_down(tmp_path, hangs=6)
    env.deps.settings_store = StaticSettings(
        budget_limits=Budgets.model_construct(
            **(Budgets().model_dump() | {"model_timeout_s": 0.01, "paused_retry_min": 240})
        )
    )
    incident = await env.open(service(env), container="shop-web-1")
    retry = Retry()
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(
            build_graph(env.deps, saver),
            env.repo,
            env.bus,
            settings=env.deps.settings_store,
            sleep=retry,
            clock=retry.clock,
        )
        await runner.start(incident)
        await paused(runner, retry)
        retry.release.set()
        await asyncio.sleep(0.05)
        await paused(runner, retry)
        assert retry.delays == [240 * 60, 240 * 60]
        await runner.close()


async def test_a_failed_run_forgets_its_pauses(tmp_path):
    env = await model_down(tmp_path, hangs=3)
    incident = await env.open(service(env), container="shop-web-1")
    retry = Retry()
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(
            build_graph(env.deps, saver),
            env.repo,
            env.bus,
            settings=env.deps.settings_store,
            sleep=retry,
        )
        await runner.start(incident)
        await paused(runner, retry)
        assert runner.retrying(incident.id)

        # The retry's first status write fails, which is a bug and not an outage.
        async def broken(incident_id: str, status: str) -> None:
            raise RuntimeError("disk full")

        env.repo.set_status = broken  # type: ignore[method-assign]
        retry.release.set()
        await runner.join()
        assert not runner.retrying(incident.id)


@pytest.mark.parametrize(
    ("ok", "outcome", "shown"),
    [(True, "resolved", "verifying"), (False, "no_action", "investigating")],
)
async def test_a_restart_before_report_shows_verifying_only_after_a_passed_verify(
    tmp_path, ok, outcome, shown
):
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")
    statuses: list[str] = []
    set_status = env.repo.set_status

    async def recording(incident_id: str, status: str) -> None:
        statuses.append(status)
        await set_status(incident_id, status)

    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        graph = build_graph(env.deps, saver)
        await graph.aupdate_state(
            IncidentRunner.config(incident.id),
            {
                "incident_id": incident.id,
                "subject": incident.subject,
                "signals": [],
                "verification": VerificationResult(ok=ok, checks=[]),
                "outcome": outcome,
            },
            as_node="verify",
        )
        await env.repo.set_status(incident.id, "paused")
        env.repo.set_status = recording  # type: ignore[method-assign]
        runner = IncidentRunner(graph, env.repo, env.bus)
        await runner.start(incident)
        await runner.join()

    assert statuses[0] == shown
    if ok:
        assert "investigating" not in statuses and statuses[-1] == "resolved"


async def test_end_cancels_a_run_in_flight_closes_the_incident_and_says_why(tmp_path):
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")
    notifier = NoticeNotifier()
    env.deps.bus = BlockingBus(env.db, "diagnose")
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, notifier=notifier)
        await runner.start(incident)
        await asyncio.wait_for(env.deps.bus.reached.wait(), 5)
        await runner.end(incident.id, REMOVED, ending="removed")
        await runner.join()

        assert (await env.repo.get(incident.id)).status == "closed"
        assert await runner.recover_on_boot() == []  # a closed incident is never run again
    events = await env.bus.history(incident.id)
    assert events[-1].kind == "notice" and events[-1].payload["text"] == english(REMOVED)
    assert notifier.notices == [(incident.id, english(REMOVED))]
    assert notifier.endings == ["removed"]


async def test_end_with_spare_leaves_an_executing_incident_and_its_run_alone(tmp_path):
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")
    notifier = NoticeNotifier()
    env.deps.bus = BlockingBus(env.db, "diagnose")
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, notifier=notifier)
        await runner.start(incident)
        await asyncio.wait_for(env.deps.bus.reached.wait(), 5)
        await env.repo.set_status(incident.id, "executing")
        await runner.end(incident.id, SNOOZED, ending="quiet", spare=("executing", "verifying"))
        assert incident.id not in runner._ended
        assert not runner._runs[incident.id].cancelled()
        assert (await env.repo.get(incident.id)).status == "executing"
        await runner.close()
    texts = [e.payload.get("text") for e in await env.bus.history(incident.id)]
    assert english(SNOOZED) not in texts
    assert notifier.notices == []


async def test_an_approve_landing_while_end_closes_the_incident_never_runs_execute(
    tmp_path, acting
):
    """The review's interleaving (#194): a Telegram yes is resolved and resumed while `end`
    awaits its write. It may win the approval row, but nothing is driven: Execute never runs."""
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        approvals = env.deps.approvals
        runner = IncidentRunner(
            build_graph(env.deps, saver), env.repo, env.bus, approvals=approvals
        )
        await runner.start(incident)
        await runner.join()
        approval_id = await runner.waiting_for(incident.id)
        real = env.repo.close_unfinished
        landed: list[bool] = []

        async def close_unfinished(incident_id):
            won = await approvals.resolve(incident_id, decide("approve"), approval_id)
            landed.append(won is not None)
            if won is not None:
                await runner.resume(incident_id, won, approval_id)
            await asyncio.wait_for(runner.join(), 5)  # let the resumed run go as far as it can
            return await real(incident_id)

        env.repo.close_unfinished = close_unfinished
        await runner.end(incident.id, REMOVED, ending="removed")
        await runner.join()

    assert landed == [True]
    assert (await env.repo.get(incident.id)).status == "closed"
    assert "execute" not in await env.nodes(incident.id)


@pytest.mark.parametrize("yes_first", [True, False])
async def test_a_yes_racing_end_with_the_stored_approvals_never_runs_and_leaves_none_pending(
    tmp_path, acting, yes_first
):
    """The same interleaving over the SQLite store (#277): a Telegram yes lands just before the
    close, or just after it. Before, it wins the row and is resumed into nothing; after, the
    approval is already closed and the yes wins nothing. Either way Execute never runs and the
    store holds no pending approval for the closed incident."""
    env = await graph_env(tmp_path)
    env.deps.approvals = ApprovalStore(env.db, "test-secret")
    incident = await env.open(service(env), container="shop-web-1")
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        approvals = env.deps.approvals
        runner = IncidentRunner(
            build_graph(env.deps, saver), env.repo, env.bus, approvals=approvals
        )
        await runner.start(incident)
        await runner.join()
        approval_id = await runner.waiting_for(incident.id)
        real = env.repo.close_unfinished
        won: list = []

        async def yes() -> None:
            decision = await approvals.resolve(incident.id, decide("approve"), approval_id)
            won.append(decision)
            if decision is not None:
                await runner.resume(incident.id, decision, approval_id)

        async def close_unfinished(incident_id):
            if yes_first:
                await yes()
            closed = await real(incident_id)
            if not yes_first:
                await yes()
            return closed

        env.repo.close_unfinished = close_unfinished
        await runner.end(incident.id, LEFT_ALONE, ending="quiet")
        await runner.join()
        record = await approvals.current(incident.id)

    assert (await env.repo.get(incident.id)).status == "closed"
    assert "execute" not in await env.nodes(incident.id)
    assert await approvals.pending() == []
    if yes_first:
        assert won[0] is not None and record.decision == won[0] and not record.closed
    else:
        assert won == [None] and record.closed and record.decision is None


async def test_a_resume_that_arrives_after_end_is_not_driven(tmp_path, acting):
    """A decision won before `end`, whose resume is scheduled only after `end` returned (a
    Telegram handler between its two awaits), and a runner that does not know of the `end` (a
    restart): the closed status alone keeps Execute from running."""
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        approvals = env.deps.approvals
        runner = IncidentRunner(
            build_graph(env.deps, saver), env.repo, env.bus, approvals=approvals
        )
        await runner.start(incident)
        await runner.join()
        approval_id = await runner.waiting_for(incident.id)
        won = await approvals.resolve(incident.id, decide("approve"), approval_id)
        await runner.end(incident.id, REMOVED, ending="removed")
        await runner.resume(incident.id, won, approval_id)
        await runner.join()

        fresh = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, approvals=approvals)
        await fresh.resume(incident.id, won, approval_id)
        await fresh.join()
        assert await fresh.recover_on_boot() == []

    assert (await env.repo.get(incident.id)).status == "closed"
    assert "execute" not in await env.nodes(incident.id)


async def test_execute_refuses_to_run_for_an_incident_closed_meanwhile(tmp_path, acting):
    """Execute's own check, the last layer: the run was driven before the close."""
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        approvals = env.deps.approvals
        runner = IncidentRunner(
            build_graph(env.deps, saver), env.repo, env.bus, approvals=approvals
        )
        await runner.start(incident)
        await runner.join()
        approval_id = await runner.waiting_for(incident.id)
        won = await approvals.resolve(incident.id, decide("approve"), approval_id)
        real_get = env.repo.get

        async def closed_once_driven(incident_id):
            found = await real_get(incident_id)
            if found is not None and found.status == "executing":
                await env.repo.close_unfinished(incident_id)  # the host is removed right now
            return found

        env.repo.get = closed_once_driven
        await runner.resume(incident.id, won, approval_id)
        await runner.join()

    assert (await env.repo.get(incident.id)).status == "closed"
    assert "execute" not in await env.nodes(incident.id, "node_finished")
    assert await env.deps.action_log.steps(incident.id) == []


async def test_end_leaves_a_finished_incident_as_it_is_and_says_nothing(tmp_path):
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")
    notifier = NoticeNotifier()
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, notifier=notifier)
        await env.repo.set_status(incident.id, "resolved")
        await runner.end(incident.id, REMOVED, ending="removed")
    assert (await env.repo.get(incident.id)).status == "resolved"
    assert notifier.notices == []


async def test_end_says_when_a_step_may_already_have_run(tmp_path):
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")
    notifier = NoticeNotifier()
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, notifier=notifier)
        await env.repo.set_status(incident.id, "executing")
        await runner.end(incident.id, REMOVED, ending="removed")
    ((_, text),) = notifier.notices
    assert "may already have run" in text


class SwitchQuiet:
    def __init__(self) -> None:
        self.quiet = False

    async def is_quiet(self, subject) -> bool:
        return self.quiet


def verification_of(monkeypatch, ok: bool) -> None:
    """Verify returns `ok` and, as it does, the service is made quiet."""

    def make(deps):
        async def node(state):
            deps.quiet.quiet = True
            result = VerificationResult(ok=ok, checks=[])
            return {"verification": result, "outcome": "resolved" if ok else None}

        return node

    monkeypatch.setattr(verify, "make", make)


async def approve_while_quiet(tmp_path, env, incident, notifier):
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, notifier=notifier)
        await runner.start(incident)
        await runner.join()
        await runner.resume(incident.id, decide("approve"), await runner.waiting_for(incident.id))
        await runner.join()


async def test_a_failed_check_on_a_quiet_service_closes_the_incident_instead_of_a_new_plan(
    tmp_path, acting, monkeypatch
):
    env = await graph_env(tmp_path)
    env.deps.quiet = SwitchQuiet()
    verification_of(monkeypatch, ok=False)
    incident = await env.open(service(env), container="shop-web-1")
    notifier = NoticeNotifier()

    await approve_while_quiet(tmp_path, env, incident, notifier)

    assert (await env.repo.get(incident.id)).status == "closed"
    events = await env.bus.history(incident.id)
    assert [e.payload["text"] for e in events if e.kind == "notice"] == [QUIET_RUN_NOTICE]
    assert notifier.notices == [(incident.id, QUIET_RUN_NOTICE)]
    assert notifier.endings == ["quiet"]
    assert (await env.nodes(incident.id))[-2:] == ["execute", "verify"]
    assert (await env.nodes(incident.id)).count("propose") == 1


async def test_a_passed_check_on_a_quiet_service_still_resolves(tmp_path, acting, monkeypatch):
    env = await graph_env(tmp_path)
    env.deps.quiet = SwitchQuiet()
    verification_of(monkeypatch, ok=True)
    incident = await env.open(service(env), container="shop-web-1")
    notifier = NoticeNotifier()

    await approve_while_quiet(tmp_path, env, incident, notifier)

    assert (await env.repo.get(incident.id)).status == "resolved"
    assert notifier.notices == []


async def test_a_run_stopping_for_a_quiet_incident_already_closed_says_nothing_more(tmp_path):
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")
    notifier = NoticeNotifier()
    await env.repo.close_unfinished(incident.id)  # `end` got there first
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, notifier=notifier)

        await runner._close_quiet(incident.id)

    events = await env.bus.history(incident.id)
    assert [e for e in events if e.kind == "notice"] == []
    assert notifier.notices == []


async def test_a_quiet_host_incident_is_closed_with_the_host_wording(tmp_path):
    env = await graph_env(tmp_path)
    subject = Subject(kind="host", host_id="a")
    incident = await env.open(subject, kind="host_unreachable")
    notifier = NoticeNotifier()
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, notifier=notifier)

        await runner._close_quiet(incident.id)

    assert (await env.repo.get(incident.id)).status == "closed"
    assert notifier.notices == [(incident.id, QUIET_RUN_NOTICE_HOST)]
    assert "the host" in QUIET_RUN_NOTICE_HOST


async def test_a_quiet_starting_after_the_yes_stops_the_run_at_approval(
    tmp_path, acting, monkeypatch
):
    env = await graph_env(tmp_path)
    env.deps.quiet = SwitchQuiet()
    incident = await env.open(service(env), container="shop-web-1")
    notifier = NoticeNotifier()
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, notifier=notifier)
        await runner.start(incident)
        await runner.join()
        approval_id = await runner.waiting_for(incident.id)
        await env.repo.set_status(incident.id, "executing")  # the yes was taken
        env.deps.quiet.quiet = True

        await runner.resume(incident.id, decide("approve"), approval_id)
        await runner.join()

    assert "execute" not in await env.nodes(incident.id)
    assert env.engine.acts == []
    assert (await env.repo.get(incident.id)).status == "closed"
    texts = [e.payload["text"] for e in await env.bus.history(incident.id) if e.kind == "notice"]
    assert texts == [QUIET_RUN_NOTICE]
    assert notifier.notices == [(incident.id, QUIET_RUN_NOTICE)]


# -- host-unreachable incidents wait for their host --------------------------------------------

HOST = Subject(kind="host", host_id="a")


class ChannelLog:
    """Records what the channels hear: each opening (with the status it was told) and notice."""

    def __init__(self) -> None:
        self.heard: list[tuple[str, str]] = []
        self.titles: list[str] = []  # the incident title of each opening and report
        self.endings: list[str | None] = []  # how each notice ended its incident, if it did

    async def incident_opened(self, incident) -> None:
        self.heard.append(("opened", incident.status))
        self.titles.append(incident.title)

    async def notice(self, incident_id: str, text: Shown, ending: str | None = None) -> None:
        self.heard.append(("notice", english(text)))
        self.endings.append(ending)

    async def report(self, incident, report) -> None:
        self.heard.append(("report", incident.id))
        self.titles.append(report.title)


async def unreachable(env):
    return await env.open(HOST, kind="host_unreachable", detail={"ok": False})


async def notices(env, incident_id: str) -> list[str]:
    return [e.payload["text"] for e in await env.bus.history(incident_id) if e.kind == "notice"]


async def test_a_host_unreachable_incident_calls_no_model_and_closes_when_the_host_answers(
    tmp_path,
):
    env = await graph_env(tmp_path)
    incident = await unreachable(env)
    channels = ChannelLog()
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, notifier=channels)
        await runner.start(incident)
        await runner.join()

        assert (await env.repo.get(incident.id)).status == "waiting"
        assert env.models.calls == [] and await env.nodes(incident.id) == []
        assert await notices(env, incident.id) == [WAITING_NOTICE]

        await runner.host_back("a")
        await runner.join()

    assert (await env.repo.get(incident.id)).status == "closed"
    assert env.models.calls == [] and await env.nodes(incident.id) == []
    assert await notices(env, incident.id) == [WAITING_NOTICE, BACK_NOTICE]
    assert channels.heard == [("opened", "waiting"), ("notice", BACK_NOTICE)]
    assert channels.endings == ["back"]


async def test_a_waiting_incident_stays_waiting_across_a_restart_and_closes_after_it(tmp_path):
    env = await graph_env(tmp_path)
    incident = await unreachable(env)
    never_started = await env.open(Subject(kind="host", host_id="b"), kind="host_unreachable")
    channels = ChannelLog()
    checkpoints = tmp_path / "checkpoints.sqlite"
    async with open_checkpointer(checkpoints) as saver:
        first = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, notifier=channels)
        await first.start(incident)
        await first.join()
        await first.close()  # Bleep stops while the host is still away

    async with open_checkpointer(checkpoints) as saver:
        second = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, notifier=channels)
        # The one that opened just before the stop waits now; the waiting one is left as it is.
        assert await second.recover_on_boot() == [never_started.id]
        await second.join()
        assert (await env.repo.get(incident.id)).status == "waiting"
        assert (await env.repo.get(never_started.id)).status == "waiting"
        await second.host_back("a")
        await second.join()

    assert (await env.repo.get(incident.id)).status == "closed"
    assert env.models.calls == []
    assert await env.nodes(incident.id) == [] and await env.nodes(never_started.id) == []
    assert channels.heard.count(("opened", "waiting")) == 2  # once each, not again on the boot
    assert await notices(env, incident.id) == [WAITING_NOTICE, BACK_NOTICE]


async def test_the_hosts_return_never_closes_an_incident_of_another_kind(tmp_path):
    env = await graph_env(tmp_path)
    disk = await env.open(HOST, kind="disk", detail={"percent": 97})
    shop = await env.open(service(env), container="shop-web-1")
    await env.repo.set_status(disk.id, "investigating")
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus)
        await runner.host_back("a")
        await runner.join()

    assert (await env.repo.get(disk.id)).status == "investigating"
    assert (await env.repo.get(shop.id)).status == "open"
    assert await notices(env, disk.id) == [] and await notices(env, shop.id) == []


async def test_a_waiting_incident_that_took_another_signal_runs_when_the_host_answers(tmp_path):
    env = await graph_env(tmp_path)
    incident = await unreachable(env)
    channels = ChannelLog()
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, notifier=channels)
        await runner.start(incident)
        await runner.join()
        disk = Signal(subject=HOST, kind="disk", at=incident.opened_at, detail={"percent": 97})
        await env.repo.open_or_attach(HOST, [disk])  # read just as the host came back
        await runner.host_back("a")
        await runner.join()

    assert await env.nodes(incident.id) == M2_PATH  # now there is a host to look at
    assert BACK_NOTICE not in await notices(env, incident.id)
    assert channels.heard == [("opened", "waiting")]  # not announced a second time
    assert (await env.repo.get(incident.id)).status == "closed"


async def test_the_watchdog_leaves_a_waiting_incident_alone(tmp_path):
    env = await graph_env(tmp_path)
    incident = await unreachable(env)
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus)
        await runner.start(incident)
        await runner.join()
    watchdog = Watchdog(env.repo, env.bus, ChannelLog(), StaticSettings())
    assert await watchdog.tick(incident.opened_at + timedelta(days=1)) == []


@pytest.mark.parametrize(
    ("answered", "closed"),
    [
        (T0 + timedelta(seconds=15), True),  # a probe reached the host after the outage was seen
        (T0 - timedelta(minutes=10), False),  # before it: the outage is newer than that probe
        (None, False),  # the last probe failed, though the registry may still call it reachable
    ],
)
async def test_an_incident_closes_quietly_only_when_a_probe_reached_its_host_after_it(
    tmp_path, answered, closed
):
    env = await graph_env(tmp_path)
    incident = await unreachable(env)  # its signal is at T0
    channels = ChannelLog()
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(
            build_graph(env.deps, saver),
            env.repo,
            env.bus,
            notifier=channels,
            host_answered=lambda host_id: answered,
        )
        await runner.start(incident)
        await runner.join()

    status = (await env.repo.get(incident.id)).status
    if closed:
        assert status == "closed"
        assert await notices(env, incident.id) == [ANSWERED_NOTICE]
        assert channels.heard == []  # no false "the host does not answer"
    else:
        assert status == "waiting"
        assert channels.heard == [("opened", "waiting")]
    assert env.models.calls == []


async def test_a_host_back_before_the_run_parks_is_caught_by_the_next_good_tick(tmp_path):
    env = await graph_env(tmp_path)
    incident = await unreachable(env)
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus)
        await runner.host_back("a")  # the host's first good tick wins the race to the run
        await runner.start(incident)
        await runner.join()
        assert (await env.repo.get(incident.id)).status == "waiting"
        await runner.host_back("a")  # every good tick calls it again
        await runner.join()

    assert (await env.repo.get(incident.id)).status == "closed"


# -- a service healthy again on its own while its plan waits (#571) -----------------------------


RECOVERED = Shown.of("notice/recovered", name="shop")


async def waiting_for_a_yes(tmp_path, env, saver, channels=None):
    """An incident whose act plan waits for a yes, over the SQLite approval store."""
    env.deps.approvals = ApprovalStore(env.db, "test-secret")
    incident = await env.open(service(env), container="shop-web-1")
    runner = IncidentRunner(
        build_graph(env.deps, saver),
        env.repo,
        env.bus,
        approvals=env.deps.approvals,
        notifier=channels,
    )
    await runner.start(incident)
    await runner.join()
    assert (await env.repo.get(incident.id)).status == "awaiting_approval"
    return runner, incident, await runner.waiting_for(incident.id)


async def test_a_service_healthy_again_closes_its_waiting_incident_once_and_runs_nothing(
    tmp_path, acting
):
    env = await graph_env(tmp_path)
    channels = ChannelLog()
    finished: list[str] = []

    async def heard_finished(incident_id: str) -> None:
        finished.append(incident_id)

    env.repo.on_finished(heard_finished)  # how the Telegram buttons go (#291)
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner, incident, approval_id = await waiting_for_a_yes(tmp_path, env, saver, channels)
        approvals = env.deps.approvals

        assert await runner.healthy_again(incident.id, RECOVERED)
        assert not await runner.healthy_again(incident.id, RECOVERED)  # the next tick's try
        late = await approvals.resolve(incident.id, decide("approve"), approval_id)
        await runner.resume(incident.id, decide("approve"), approval_id)
        await runner.join()
        record = await approvals.current(incident.id)

    assert (await env.repo.get(incident.id)).status == "closed"
    assert late is None and record.closed and record.decision is None
    assert await approvals.pending() == []  # no reminder comes for it
    assert await approvals.grant(incident.id) is None
    assert "execute" not in await env.nodes(incident.id)
    assert await notices(env, incident.id) == [english(RECOVERED)]
    assert channels.heard == [("opened", "open"), ("notice", english(RECOVERED))]
    assert channels.endings == ["recovered"]
    assert finished == [incident.id]


async def test_the_recovery_notice_says_nothing_was_changed_and_never_fixed():
    for language in ("en", "de"):
        shown = prompts.show(RECOVERED, language)
        assert "shop" in shown and "fixed" not in shown.lower() and "behoben" not in shown.lower()
    assert "nothing was changed" in english(RECOVERED)
    assert "was not run" in english(RECOVERED)


async def test_a_yes_that_won_before_the_recovery_close_runs_its_plan(tmp_path, acting):
    """The yes won the approval; its resume is not driven yet, so the incident still says
    `awaiting_approval`. The recovery close must not take it: the person was told it runs."""
    env = await graph_env(tmp_path)
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner, incident, approval_id = await waiting_for_a_yes(tmp_path, env, saver)
        won = await env.deps.approvals.resolve(incident.id, decide("approve"), approval_id)

        assert not await runner.healthy_again(incident.id, RECOVERED)
        await runner.resume(incident.id, won, approval_id)
        await runner.join()

    assert "execute" in await env.nodes(incident.id)
    assert english(RECOVERED) not in await notices(env, incident.id)


@pytest.mark.parametrize("yes_first", [True, False])
async def test_a_yes_and_the_recovery_close_at_once_leave_exactly_one_outcome(
    tmp_path, acting, yes_first
):
    env = await graph_env(tmp_path)
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner, incident, approval_id = await waiting_for_a_yes(tmp_path, env, saver)
        yes = env.deps.approvals.resolve(incident.id, decide("approve"), approval_id)
        close = runner.healthy_again(incident.id, RECOVERED)
        if yes_first:
            won, closed = await asyncio.gather(yes, close)
        else:
            closed, won = await asyncio.gather(close, yes)
        if won is not None:
            await runner.resume(incident.id, won, approval_id)
        await runner.join()

    assert (won is not None) != closed
    ran = "execute" in await env.nodes(incident.id)
    told = english(RECOVERED) in await notices(env, incident.id)
    assert (ran, told) == (won is not None, closed)


@pytest.mark.parametrize("status", ["open", "investigating", "paused", "executing", "verifying"])
async def test_the_recovery_close_takes_only_an_incident_waiting_for_a_yes(tmp_path, status):
    env = await graph_env(tmp_path)
    env.deps.approvals = ApprovalStore(env.db, "test-secret")
    incident = await env.open(service(env), container="shop-web-1")
    await env.deps.approvals.request(incident.id, Proposal(kind="no_action", reason="r"), [])
    await env.repo.set_status(incident.id, status)
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus)
        assert not await runner.healthy_again(incident.id, RECOVERED)

    assert (await env.repo.get(incident.id)).status == status
    assert await notices(env, incident.id) == []


async def test_an_incident_whose_earlier_plan_ran_a_step_is_not_closed_as_recovered(
    tmp_path, acting
):
    """Back at Approval after Verify's loop-back: something was changed, so "nothing was
    changed" would be false."""
    env = await graph_env(tmp_path)
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner, incident, _ = await waiting_for_a_yes(tmp_path, env, saver)
        step = PlanStep(action="docker.restart", host_id="a", target="shop-web-1")
        await env.deps.action_log.start(incident.id, "earlier:plan", 0, step)

        assert not await runner.healthy_again(incident.id, RECOVERED)

    assert (await env.repo.get(incident.id)).status == "awaiting_approval"
    assert (await env.deps.approvals.current(incident.id)).closed is False


# -- folded repeats ------------------------------------------------------------------------------


async def test_a_repeat_is_not_announced_but_what_came_of_it_is_told_with_its_count(tmp_path):
    env = await graph_env(tmp_path)
    channels = ChannelLog()
    env.deps.notifier = channels  # the Report node tells the channels too
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, notifier=channels)
        first = await env.open(service(env), container="shop-web-1")
        await runner.start(first)
        await runner.join()
        repeat = await env.open(service(env), container="shop-web-1")
        assert repeat.fold_of == first.id
        await runner.start(repeat)
        await runner.join()

    assert await env.nodes(repeat.id) == M2_PATH  # it is looked at like any incident
    assert channels.heard == [
        ("opened", "open"),
        ("report", first.id),
        ("report", repeat.id),  # no "opened" for the repeat, but its outcome (no action)
    ]
    assert "(seen 2 times since" in channels.titles[-1]


async def test_a_host_that_flaps_then_stays_down_is_told_of_its_second_wait(tmp_path):
    env = await graph_env(tmp_path)
    channels = ChannelLog()
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, notifier=channels)
        flap = await unreachable(env)
        await runner.start(flap)
        await runner.join()
        await runner.host_back("a")  # it answered again
        down = await unreachable(env)  # then it went down for good
        assert down.fold_of == flap.id
        await runner.start(down)
        await runner.join()

    assert (await env.repo.get(down.id)).status == "waiting"
    assert channels.heard == [
        ("opened", "waiting"),
        ("notice", BACK_NOTICE),
        ("opened", "waiting"),  # the repeat's wait is told, with its count
    ]
    assert "(seen 2 times since" in channels.titles[-1]


async def test_a_repeat_of_a_host_that_comes_back_again_ends_with_back_too(tmp_path):
    env = await graph_env(tmp_path)
    channels = ChannelLog()
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, notifier=channels)
        flap = await unreachable(env)
        await runner.start(flap)
        await runner.join()
        await runner.host_back("a")
        again = await unreachable(env)
        assert again.fold_of == flap.id
        await runner.start(again)
        await runner.join()
        await runner.host_back("a")

    assert channels.heard[-1] == ("notice", BACK_NOTICE)  # every thread ends (#380)
    assert channels.endings == ["back", "back"]


async def test_a_repeats_failed_run_says_its_count(tmp_path, monkeypatch):
    env = await graph_env(tmp_path)
    channels = ChannelLog()
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus, notifier=channels)
        first = await env.open(service(env), container="shop-web-1")
        await env.repo.close_unfinished(first.id)
        repeat = await env.open(service(env), container="shop-web-1")

        async def broken(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(runner.graph, "ainvoke", broken)
        await runner.start(repeat)
        await runner.join()

    [(kind, text)] = channels.heard
    assert kind == "notice" and "RuntimeError" in text and "seen 2 times since" in text
