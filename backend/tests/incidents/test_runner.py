import asyncio
from datetime import UTC, datetime

import pytest
from langchain_core.callbacks import BaseCallbackHandler

from bleep import prompts
from bleep.graph.build import build_graph, open_checkpointer
from bleep.graph.deps import StaticSettings
from bleep.graph.nodes import propose
from bleep.incidents.approvals import ApprovalStore
from bleep.incidents.bus import EventBus
from bleep.incidents.runner import IncidentRunner
from bleep.schemas.graph import Decision, PlanStep, Proposal, VerificationPlan
from bleep.schemas.incidents import Subject
from bleep.schemas.settings import Budgets
from fakes import FakeChatModel
from graph_env import confident_model, graph_env, no_wait

M2_PATH = ["gather", "investigate", "diagnose", "propose", "report"]


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
        self.fail = fail

    async def incident_opened(self, incident) -> None:
        return None

    async def notice(self, incident_id: str, text: str) -> None:
        if self.fail:
            raise RuntimeError("telegram is down")
        self.notices.append((incident_id, text))


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


class Retry:
    """The runner's `sleep`: records each wait and holds the retry until the test lets it go."""

    def __init__(self) -> None:
        self.delays: list[float] = []
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)
        self.waiting.set()
        await self.release.wait()
        self.release.clear()
        self.waiting.clear()


async def model_down(tmp_path, hangs: int):
    """An environment whose model hangs for `hangs` calls, cut off after 10 ms with no waiting."""
    env = await graph_env(tmp_path)
    script = confident_model().script
    env.models.model = HangingModel(script=script, structured_output_unsupported=True, hangs=hangs)
    env.deps.settings_store = StaticSettings(budget_limits=Budgets(model_timeout_s=0.01))
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
        assert retry.delays == [300, 300]


async def test_recover_on_boot_picks_up_a_paused_run(tmp_path):
    env = await graph_env(tmp_path)
    incident = await env.open(service(env), container="shop-web-1")
    await env.repo.set_status(incident.id, "paused")
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        runner = IncidentRunner(build_graph(env.deps, saver), env.repo, env.bus)
        assert await runner.recover_on_boot() == [incident.id]
        await runner.join()
    assert (await env.repo.get(incident.id)).status == "closed"
