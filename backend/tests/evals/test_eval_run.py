"""The harness on a fake engine, a scripted model and a fake judge: a fault opens the scenario's
incident, the graph stops after Propose, the fault goes off whatever happens, and the run is
measured and scored."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from openai.types.chat import ChatCompletion

from bleep.hosts.config import HostConfig
from bleep.schemas.docker import DockerEvent
from bleep.schemas.graph import Diagnosis
from bleep.schemas.incidents import Incident, Subject
from bleep.schemas.settings import ModelSettings
from evals.judge import JudgeError, Judgement, ManualJudge
from evals.report import RunInfo, append_jsonl, write_info
from evals.run import (
    REPO,
    DemoFaults,
    EvalError,
    EvalSettings,
    FaultError,
    IncidentCatcher,
    ShopReset,
    demo_containers,
    docker_endpoint,
    earlier_batch,
    eval_host,
    fault_docker_host,
    live_backend,
    main,
    out_of_context,
    report_for,
    run_scenario,
    run_suite,
    shop_items_url,
    suite_scenarios,
    summary,
)
from evals.scenarios import ExpectedAction, ExpectedStep, Scenario, ScenarioResult
from fakes import FakeChatModel, FakeEngine
from graph_env import box

SERVICE = "demo/compose:shop"
SCENARIO = Scenario.model_validate(
    {
        "id": "db-down",
        "fault": "db-down",
        "subject": {"kind": "service", "host_id": "demo", "service_id": SERVICE},
        "expected": {
            "root_cause": "shop-db is stopped",
            "category": "dependency",
            "action": {"kind": "act", "steps": [{"action": "docker.start", "target": "shop-db"}]},
            "must_not": ["docker.restart"],
        },
        "timeout_s": 20,
    }
)
# No URLs: a whole run would check them, and tests never touch the network.
HOSTS = """
hosts:
  - id: demo
    name: Demo stack
    kind: socket
    endpoint: tcp://socket-proxy:2375
    rules: [shop-db holds the shop's items]
"""
URLS = """
    services:
      shop:
        urls:
          - url: http://host.docker.internal:8080/health
          - url: http://shop-web:8000/health
          - url: http://m1.local:8080/api/items
"""


def settings(tmp_path: Path, hosts_text: str = HOSTS, **fields) -> EvalSettings:
    hosts = tmp_path / "hosts.yaml"
    hosts.write_text(hosts_text, encoding="utf-8")
    models = ModelSettings(provider="local", local_model="tiny-local")
    base = {
        "hosts_file": hosts,
        "docker_endpoint": "unix:///fake.sock",
        "watch_interval_s": 0.05,
        "settle_s": 7,
        "judge_model": "fake-judge",
        "models": models,
    }
    return EvalSettings(_env_file=None, **(base | fields))  # type: ignore[call-arg]


def engine() -> FakeEngine:
    return FakeEngine(
        "demo",
        [
            box("shop-web", "shop", health="healthy"),
            box("shop-db", "shop", state="exited"),  # db-down: the plan starts it
            box("blog-web", "blog"),
        ],
    )


def unhealthy(name: str) -> DockerEvent:
    return DockerEvent(
        time=datetime.now(UTC),
        type="container",
        action="health_status: unhealthy",
        container_id=f"id-{name}",
        container_name=name,
    )


class FakeFaults:
    """`on` stops shop-db and makes the blog, then the shop, unhealthy; `off` is recorded."""

    def __init__(self, engine: FakeEngine, symptoms: list[str] | None = None) -> None:
        self.engine = engine
        self.symptoms = ["blog-web", "shop-web"] if symptoms is None else symptoms
        self.calls: list[tuple[str, str]] = []

    async def switch(self, fault: str, state: Literal["on", "off"]) -> None:
        self.calls.append((fault, state))
        if state == "on":
            for name in self.symptoms:
                self.engine.emit(unhealthy(name))


class FakeJudge:
    name = "fake-judge"

    def __init__(self, error: bool = False) -> None:
        self.error = error
        self.calls: list[tuple[str, Diagnosis]] = []

    async def judge(self, expected: str, diagnosis: Diagnosis) -> Judgement:
        self.calls.append((expected, diagnosis))
        if self.error:
            raise JudgeError("no answer")
        return Judgement(score=0.8, passed=True, reason="names the stopped db", usd=0.001)


def reply(content: str = "", tokens_in: int = 1000, **fields) -> AIMessage:
    usage = {"input_tokens": tokens_in, "output_tokens": 50, "total_tokens": tokens_in + 50}
    return AIMessage(content, usage_metadata=usage, **fields)


def script() -> list[AIMessage]:
    diagnosis = {"root_cause": "shop-db is stopped", "category": "dependency", "confidence": 0.9}
    step = {"action": "docker.start", "host_id": "demo", "target": "shop-db"}
    plan = {"kind": "act", "steps": [step], "reason": "start the db", "verification": {}}
    return [
        reply(tool_calls=[{"name": "list_containers", "args": {}, "id": "call-1"}], tokens_in=1200),
        reply("shop-db is not running", tokens_in=2500),
        reply(json.dumps(diagnosis), tokens_in=3000),
        reply(json.dumps(plan), tokens_in=2800),
    ]


class OutOfContext(FakeChatModel):
    """Answers the first `answers` calls from its script, then reaches its length limit the way
    a reasoning model with a small context does: openai's `LengthFinishReasonError`."""

    answers: int = 2

    async def _agenerate(self, messages: list[BaseMessage], *args, **kwargs) -> ChatResult:
        if self.cursor >= self.answers:
            raise length_error()
        return await super()._agenerate(messages, *args, **kwargs)


def length_error() -> openai.LengthFinishReasonError:
    completion = ChatCompletion(
        id="cut-off", choices=[], created=0, model="tiny-local", object="chat.completion"
    )
    return openai.LengthFinishReasonError(completion=completion)


class Models:
    def __init__(self, model: FakeChatModel) -> None:
        self.model = model

    def __call__(self, role: str) -> FakeChatModel:
        return self.model


def connector(fake: FakeEngine):
    async def connect(_host: HostConfig):
        return None, fake

    return connect


async def test_a_run_stops_after_propose_and_is_measured_and_scored(tmp_path):
    fake = engine()
    faults, judge = FakeFaults(fake), FakeJudge()
    model = FakeChatModel(script=script(), structured_output_unsupported=True)

    result = await run_scenario(
        SCENARIO,
        settings(tmp_path),
        faults=faults,
        judge=judge,
        connector=connector(fake),
        models=Models(model),
    )

    assert result.error is None
    assert faults.calls == [("db-down", "on"), ("db-down", "off")]
    assert fake.acts == []  # nothing was executed
    assert model.cursor == 4  # investigate twice, diagnose, propose; no approval, no report
    assert result.diagnosis is not None and result.diagnosis.root_cause == "shop-db is stopped"
    assert result.proposal is not None and result.proposal.kind == "act"
    assert result.action_correct and not result.unnecessary_action and result.category_correct
    assert result.steps == 1
    assert result.tokens == 1200 + 2500 + 3000 + 2800 + 4 * 50
    assert result.peak_prompt_tokens == 3000
    assert result.usd == 0.0  # local: free
    assert result.model == "tiny-local" and result.judge == "fake-judge"
    assert result.seconds_to_incident is not None and result.seconds_to_diagnosis is not None
    assert 0 <= result.seconds_to_incident <= result.seconds_to_diagnosis
    assert judge.calls == [("shop-db is stopped", result.diagnosis)]
    assert (result.root_cause_score, result.root_cause_pass) == (0.8, True)
    assert result.judge_usd == 0.001
    assert result.structured_fallbacks == 2  # Diagnose and Propose: the model has no tools
    assert summary(result).startswith("db-down run 1: root cause 0.80, plan exact, 1 tool call,")
    assert summary(result).endswith("2 structured-output fallbacks")


async def test_no_incident_in_time_is_an_error_and_the_fault_still_goes_off(tmp_path):
    fake = engine()
    faults, judge = FakeFaults(fake, symptoms=["blog-web"]), FakeJudge()
    quick = SCENARIO.model_copy(update={"timeout_s": 1})

    result = await run_scenario(
        quick, settings(tmp_path), faults=faults, judge=judge, connector=connector(fake),
        models=Models(FakeChatModel(script=[])),
    )  # fmt: skip

    assert result.error == "timed out after 1 s waiting for the incident"
    assert faults.calls == [("db-down", "on"), ("db-down", "off")]
    assert result.incident_id is None and result.diagnosis is None
    assert not result.action_correct and not result.unnecessary_action
    assert judge.calls == []


async def test_a_failing_model_is_an_error_and_the_fault_still_goes_off(tmp_path):
    fake = engine()
    faults = FakeFaults(fake)

    result = await run_scenario(
        SCENARIO, settings(tmp_path), faults=faults, judge=FakeJudge(), connector=connector(fake),
        models=Models(FakeChatModel(script=[])),
    )  # fmt: skip

    assert faults.calls[-1] == ("db-down", "off")
    assert result.error is not None and "script exhausted" in result.error
    assert result.incident_id is not None and result.seconds_to_incident is not None
    assert result.diagnosis is None and result.proposal is None
    assert result.root_cause_score is None


async def test_a_model_out_of_context_is_a_failed_run_with_its_reason(tmp_path):
    fake = engine()
    faults, judge = FakeFaults(fake), FakeJudge()
    model = OutOfContext(script=script(), structured_output_unsupported=True)

    result = await run_scenario(
        SCENARIO, settings(tmp_path), faults=faults, judge=judge, connector=connector(fake),
        models=Models(model),
    )  # fmt: skip

    assert result.failure == "context_length"
    assert result.error is not None and result.error.startswith("LengthFinishReasonError:")
    assert faults.calls[-1] == ("db-down", "off")
    assert result.incident_id is not None and result.steps == 1  # Investigate's work is kept
    assert result.diagnosis is None and result.root_cause_score is None and judge.calls == []
    assert "failed, out of context" in summary(result)


async def test_other_failures_say_how_the_run_ended(tmp_path):
    fake = engine()
    failed = await run_scenario(
        SCENARIO, settings(tmp_path), faults=FakeFaults(fake), judge=FakeJudge(),
        connector=connector(fake), models=Models(FakeChatModel(script=[])),
    )  # fmt: skip
    late = await run_scenario(
        SCENARIO.model_copy(update={"timeout_s": 1}), settings(tmp_path),
        faults=FakeFaults(engine(), symptoms=[]), judge=FakeJudge(), connector=connector(engine()),
        models=Models(FakeChatModel(script=[])),
    )  # fmt: skip
    assert (failed.failure, late.failure) == ("error", "timeout")


def test_a_length_limit_is_found_behind_a_wrapping_error():
    try:
        try:
            raise length_error()
        except openai.LengthFinishReasonError as cut:
            raise RuntimeError("the node failed") from cut
    except RuntimeError as wrapped:
        assert out_of_context(wrapped)
    assert out_of_context(ExceptionGroup("runs", [ValueError("x"), length_error()]))
    assert not out_of_context(RuntimeError("the node failed"))


async def test_a_judge_that_fails_leaves_the_score_empty_and_the_exact_checks_standing(tmp_path):
    fake = engine()
    model = FakeChatModel(script=script(), structured_output_unsupported=True)

    result = await run_scenario(
        SCENARIO, settings(tmp_path), faults=FakeFaults(fake), judge=FakeJudge(error=True),
        connector=connector(fake), models=Models(model),
    )  # fmt: skip

    assert result.root_cause_score is None and result.root_cause_pass is False
    assert result.judge_reason == "the judge failed: no answer"
    assert result.action_correct is True


async def test_the_suite_numbers_the_runs_settles_between_them_and_hands_each_result_on(
    tmp_path,
):
    waits: list[float] = []
    handed: list[tuple[str, int]] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    class Bench:
        """Each run connects afresh, as the real one does: a new engine and a new model."""

        def __init__(self) -> None:
            self.faults = FakeFaults(engine())
            self.models = Models(FakeChatModel(script=[]))

        async def connect(self, _host: HostConfig):
            self.faults.engine = engine()
            self.models.model = FakeChatModel(script=script(), structured_output_unsupported=True)
            return None, self.faults.engine

    bench = Bench()
    results = await run_suite(
        [SCENARIO], settings(tmp_path), 2, faults=bench.faults, judge=FakeJudge(),
        on_result=lambda r: handed.append((r.id, r.run)), connector=bench.connect,
        models=bench.models, sleep=sleep,
    )  # fmt: skip

    assert [r.error for r in results] == [None, None]
    assert [r.run for r in results] == [1, 2]
    assert handed == [("db-down", 1), ("db-down", 2)]
    assert waits == [7]


async def test_with_a_reset_the_suite_resets_the_shop_before_every_run_instead_of_settling(
    tmp_path,
):
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    faults = FakeFaults(engine())
    models = Models(FakeChatModel(script=[]))

    async def reset() -> None:
        faults.calls.append(("reset", ""))

    async def connect(_host: HostConfig):  # each run connects afresh, as the real one does
        faults.engine = engine()
        models.model = FakeChatModel(script=script(), structured_output_unsupported=True)
        return None, faults.engine

    results = await run_suite(
        [SCENARIO], settings(tmp_path), 2, faults=faults, judge=FakeJudge(), connector=connect,
        models=models, sleep=sleep, reset=reset,
    )  # fmt: skip

    assert [r.error for r in results] == [None, None]
    assert faults.calls == [
        ("reset", ""), ("db-down", "on"), ("db-down", "off"),
        ("reset", ""), ("db-down", "on"), ("db-down", "off"),
    ]  # fmt: skip
    assert waits == []  # the reset waits until the shop answers; no settle on top


async def test_a_later_batch_numbers_its_runs_after_the_earlier_ones(tmp_path):
    fake = engine()

    async def connect(_host: HostConfig):
        return None, fake

    model = FakeChatModel(script=script(), structured_output_unsupported=True)
    results = await run_suite(
        [SCENARIO], settings(tmp_path), 1, faults=FakeFaults(fake), judge=FakeJudge(),
        connector=connect, models=Models(model), done={"db-down": 2, "hung": 3},
    )  # fmt: skip

    assert [(r.id, r.run, r.error) for r in results] == [("db-down", 3, None)]


def test_an_earlier_batch_gives_its_runs_and_when_it_started(tmp_path):
    raw = tmp_path / "scenarios-20261103-104336.jsonl"
    for run in (1, 2):
        append_jsonl(raw, ScenarioResult(id="db-down", run=run, model="tiny-local", judge="j"))
    now = datetime(2026, 11, 3, 13, 0, tzinfo=UTC)

    earlier, started = earlier_batch(raw, now, "tiny-local")

    assert [r.run for r in earlier] == [1, 2]
    assert started == datetime(2026, 11, 3, 10, 43, 36, tzinfo=UTC)
    renamed = raw.rename(tmp_path / "mine.jsonl")
    assert earlier_batch(renamed, now, "tiny-local")[1] == now


def test_an_earlier_batch_must_exist_and_be_of_the_same_model(tmp_path):
    with pytest.raises(EvalError, match="no earlier batch"):
        earlier_batch(tmp_path / "none.jsonl", datetime.now(UTC), "tiny-local")
    raw = tmp_path / "scenarios-20261103-104336.jsonl"
    append_jsonl(raw, ScenarioResult(id="db-down", model="vendor/big", judge="j"))
    with pytest.raises(EvalError, match="vendor/big, not of tiny-local"):
        earlier_batch(raw, datetime.now(UTC), "tiny-local")


def test_main_refuses_a_missing_earlier_batch_before_any_fault(tmp_path, capsys):
    missing = settings(tmp_path, fault_script=REPO / "demo" / "fault.sh")
    assert main(["--append", str(tmp_path / "none.jsonl")], settings=missing) == 2
    assert "no earlier batch" in capsys.readouterr().err


def test_the_demo_host_is_reached_from_this_machine(tmp_path, caplog):
    host = eval_host(settings(tmp_path, HOSTS + URLS))
    assert host.endpoint == "unix:///fake.sock"
    assert host.rules == ["shop-db holds the shop's items"]
    assert [u.url for u in host.services["shop"].urls] == [
        "http://localhost:8080/health",  # host.docker.internal:8080 is shop-proxy's port here
        "http://shop-web:8000/health",  # a container's name: left as it is, and logged
        "http://m1.local:8080/api/items",  # another machine answers here too
    ]
    warned = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert warned == ["http://shop-web:8000/health may not answer from this machine; its checks "
                      "may fail"]  # fmt: skip


def test_the_shipped_demo_host_is_the_shop_on_port_8080(tmp_path):
    shipped = settings(tmp_path, hosts_file=REPO / "config" / "hosts.demo.yaml")
    host = eval_host(shipped)
    assert list(host.services) == ["shop"]
    assert [u.url for u in host.services["shop"].urls] == [
        "http://localhost:8080/health",
        "http://localhost:8080/api/items",
    ]


def test_an_unknown_host_is_refused(tmp_path):
    with pytest.raises(EvalError, match="demo"):
        eval_host(settings(tmp_path, host_id="nope"))


def test_the_docker_endpoint_falls_back_to_docker_host(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "unix:///from/env.sock")
    assert docker_endpoint(settings(tmp_path, docker_endpoint=None)) == "unix:///from/env.sock"
    assert docker_endpoint(settings(tmp_path)) == "unix:///fake.sock"


def test_the_local_model_server_is_reached_from_this_machine(tmp_path):
    models = ModelSettings(provider="local", local_base_url="http://host.docker.internal:1235/v1")
    assert settings(tmp_path, models=models).models.local_base_url == "http://localhost:1235/v1"


async def test_the_demo_hosts_containers_are_listed(tmp_path):
    listed = await demo_containers(settings(tmp_path), connector(engine()))
    assert [c.name for c in listed] == ["shop-web", "shop-db", "blog-web"]


@pytest.mark.parametrize("hosts_text", [HOSTS, HOSTS + URLS])
async def test_an_unreachable_engine_is_refused(tmp_path, hosts_text):
    async def refuse(_host: HostConfig):
        raise ConnectionRefusedError("no docker here")

    with pytest.raises(EvalError, match="unix:///fake.sock"):
        await demo_containers(settings(tmp_path, hosts_text), refuse)


def test_a_running_bleep_backend_is_noticed():
    backend = {"bleep.self": "true", "com.docker.compose.service": "backend"}
    assert live_backend([box("bleep-backend-1", labels=backend)])
    assert not live_backend([box("bleep-backend-1", labels=backend, state="exited")])
    assert not live_backend([box("shop-web", "shop")])


def test_the_model_flag_names_the_local_model_when_the_provider_is_local(tmp_path):
    assert settings(tmp_path).with_model("other").models.local_model == "other"
    cloud = settings(tmp_path, models=ModelSettings(provider="openrouter"))
    assert cloud.with_model("vendor/big").models.model == "vendor/big"


def test_main_refuses_a_judge_that_is_the_model_under_test_before_any_fault(tmp_path, capsys):
    same = settings(tmp_path, judge_model="tiny-local", fault_script=tmp_path / "never.sh")
    assert main([], settings=same) == 2
    assert "is the model under test" in capsys.readouterr().err


def test_main_refuses_an_unknown_scenario(tmp_path, capsys):
    assert main(["--only", "nope"], settings=settings(tmp_path)) == 2
    assert "no scenario 'nope'" in capsys.readouterr().err


async def test_the_fault_script_gets_the_fault_and_the_state(tmp_path):
    out = tmp_path / "calls"
    script = tmp_path / "fault.sh"
    script.write_text(f'#!/bin/sh\necho "$1 $2" >> {out}\n', encoding="utf-8")
    os.chmod(script, 0o755)
    await DemoFaults(script).switch("db-down", "on")
    await DemoFaults(script).switch("db-down", "off")
    assert out.read_text(encoding="utf-8").splitlines() == ["db-down on", "db-down off"]


async def test_the_fault_script_acts_on_the_engine_the_harness_watches(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "unix:///from/the/shell.sock")
    out = tmp_path / "docker_host"
    script = tmp_path / "fault.sh"
    script.write_text(f'#!/bin/sh\necho "$DOCKER_HOST" >> {out}\n', encoding="utf-8")
    os.chmod(script, 0o755)
    socket_host = eval_host(settings(tmp_path))
    await DemoFaults(script, fault_docker_host(settings(tmp_path), socket_host)).switch(
        "hung", "on"
    )
    await DemoFaults(script).switch("hung", "off")  # an SSH host: the shell's DOCKER_HOST
    assert out.read_text(encoding="utf-8").splitlines() == [
        "unix:///fake.sock",
        "unix:///from/the/shell.sock",
    ]


def test_an_ssh_host_leaves_the_fault_scripts_docker_host_alone(tmp_path):
    ssh = HostConfig(
        id="m1", name="M1", kind="ssh", address="m1.local", user="me", key_file="/k",
        known_hosts_file="/kh",
    )  # fmt: skip
    assert fault_docker_host(settings(tmp_path), ssh) is None


async def test_a_failing_fault_script_is_a_fault_error(tmp_path):
    script = tmp_path / "fault.sh"
    script.write_text("#!/bin/sh\necho 'no such fault' >&2\nexit 3\n", encoding="utf-8")
    os.chmod(script, 0o755)
    with pytest.raises(FaultError, match="no such fault"):
        await DemoFaults(script).switch("nope", "on")


async def test_a_manual_judge_leaves_the_root_cause_pending_and_the_exact_checks_standing(tmp_path):
    fake = engine()
    model = FakeChatModel(script=script(), structured_output_unsupported=True)

    result = await run_scenario(
        SCENARIO, settings(tmp_path), faults=FakeFaults(fake), judge=ManualJudge(),
        connector=connector(fake), models=Models(model),
    )  # fmt: skip

    assert result.judge == "manual" and result.diagnosis is not None
    assert result.root_cause_score is None and result.root_cause_pass is False
    assert result.judge_reason is None and result.judge_usd is None
    assert result.action_correct is True
    assert "root cause pending" in summary(result)


def test_the_judge_flag_picks_the_manual_judge_over_the_settings(tmp_path, capsys):
    same = settings(tmp_path, judge_model="tiny-local", fault_script=REPO / "demo" / "fault.sh")
    assert main(["--judge", "manual", "--only", "nope"], settings=same) == 2
    err = capsys.readouterr().err
    assert "no scenario 'nope'" in err and "model under test" not in err


def test_a_later_batch_rewrites_the_report_of_the_earlier_ones(tmp_path):
    reports = tmp_path / "docs" / "evals"
    raw = tmp_path / "scenarios-20261103-104336.jsonl"
    started = datetime(2026, 11, 3, 10, 43, 36, tzinfo=UTC)
    first = report_for(raw, started, reports)
    assert first == reports / "scenarios-2026-11-03.md"

    info = RunInfo(
        started_at=started, finished_at=started, model="tiny-local", provider="local",
        judge="manual", repeat=1, scenarios=[SCENARIO], command="x", raw=str(raw),
        report=str(first),
    )  # fmt: skip
    write_info(raw, info)
    reports.mkdir(parents=True)
    first.write_text("batch 1", encoding="utf-8")
    assert report_for(raw, started, reports) == first


async def test_the_catcher_skips_an_incident_opened_before_the_fault():
    """An incident the watcher opened on the shop before the fault went on (a leftover of the
    last run) is not this run's: its time to incident would be negative."""
    catcher = IncidentCatcher(None, None, None)  # type: ignore[arg-type]
    subject = Subject(kind="service", host_id="demo", service_id="demo/compose:shop")
    fault_on = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)

    def opened(id_: str, seconds: int) -> Incident:
        at = fault_on + timedelta(seconds=seconds)
        return Incident(id=id_, subject=subject, status="open", opened_at=at, title="shop")

    await catcher.start(opened("stale", -5))
    await catcher.start(opened("fresh", 40))
    assert (await catcher.wait_for(subject, since=fault_on)).id == "fresh"


# --- the shop reset (#104) ----------------------------------------------------------------------

COMPOSE = Path("/repo/demo/compose.yaml")
ITEMS = "http://localhost:8080/api/items"


class FakeDockerCli:
    """Answers `docker` the way the engine does after a recreate: the health checks report
    `starting` for the first `starting` looks, then `healthy`. `up_fails` fails the recreate."""

    def __init__(self, starting: int = 1, up_fails: bool = False, never_healthy: bool = False):
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.starting = starting
        self.up_fails = up_fails
        self.never_healthy = never_healthy

    async def __call__(self, argv: list[str], stdin: str, env: dict[str, str]):
        self.calls.append((argv, env))
        if argv[1] == "compose" and self.up_fails:
            return 1, "", "Error: port 8080 is already allocated"
        if argv[1] == "compose":
            return 0, "", ""
        looks = sum(a[1] == "inspect" for a, _ in self.calls)
        if self.never_healthy or looks <= self.starting:
            return 0, "starting\nhealthy\n", ""
        return 0, "healthy\nhealthy\n", ""


def items_server(statuses: list[int]) -> tuple[httpx.AsyncClient, list[str]]:
    """A shop whose /api/items answers each status in turn, then 200."""
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(statuses.pop(0) if statuses else 200)

    return httpx.AsyncClient(transport=httpx.MockTransport(answer)), asked


async def test_the_shop_reset_recreates_every_container_and_waits_until_it_answers():
    docker = FakeDockerCli(starting=2)
    client, asked = items_server([502])
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    reset = ShopReset(
        COMPOSE, ITEMS, docker_host="unix:///fake.sock", env={"DEMO_INJECT": "1"},
        run=docker, http=client, sleep=sleep,
    )  # fmt: skip
    await reset()

    commands = [argv for argv, _ in docker.calls]
    # Every container recreated, so every log starts empty; no `down -v`: volumes are kept.
    # `-p shop` keeps a COMPOSE_PROJECT_NAME in the environment from aiming it elsewhere.
    assert commands[0] == [
        "docker", "compose", "-p", "shop", "-f", str(COMPOSE), "up", "-d", "--force-recreate"
    ]  # fmt: skip
    assert (
        commands[1:]
        == [["docker", "inspect", "-f", "{{.State.Health.Status}}", "shop-web", "shop-db"]] * 4
    )  # starting twice, then healthy around the 502
    assert not any(word in argv for argv in commands for word in ("down", "-v", "--volumes"))
    assert all(env["DOCKER_HOST"] == "unix:///fake.sock" for _, env in docker.calls)
    assert docker.calls[0][1]["DEMO_INJECT"] == "1"  # Compose reads the variant from here
    assert asked == [ITEMS, ITEMS]  # 502 first, then 200
    assert waits == [3, 3, 3, 3]


async def test_a_shop_that_does_not_come_back_stops_the_suite():
    client, _ = items_server([])
    sleep, clock = fake_time()
    reset = ShopReset(
        COMPOSE, ITEMS, run=FakeDockerCli(never_healthy=True), http=client, sleep=sleep,
        clock=clock, timeout_s=10,
    )  # fmt: skip
    with pytest.raises(EvalError, match="not back after 10 s: shop-web starting"):
        await reset()

    down, _ = items_server([503] * 99)
    sleep, clock = fake_time()
    reset = ShopReset(
        COMPOSE, ITEMS, run=FakeDockerCli(), http=down, sleep=sleep, clock=clock, timeout_s=10
    )
    with pytest.raises(EvalError, match="503"):
        await reset()


async def test_a_failed_recreate_stops_the_suite_with_composes_error():
    client, asked = items_server([])
    reset = ShopReset(COMPOSE, ITEMS, run=FakeDockerCli(up_fails=True), http=client)
    with pytest.raises(EvalError, match="port 8080 is already allocated"):
        await reset()
    assert asked == []


async def no_sleep(_seconds: float) -> None:
    return None


def fake_time() -> tuple:
    """A sleep that moves a fake clock instead of waiting, and the clock."""
    now = [0.0]

    async def sleep(seconds: float) -> None:
        now[0] += seconds

    return sleep, lambda: now[0]


async def test_the_reset_gives_up_on_time_even_when_docker_is_slow():
    """The timeout is a deadline on the clock, not a count of the sleeps between looks: a
    `docker inspect` that takes 30 s each must not stretch 60 s into ten minutes."""
    now = [0.0]
    docker = FakeDockerCli(never_healthy=True)

    async def slow(argv: list[str], stdin: str, env: dict[str, str]):
        now[0] += 30
        return await docker(argv, stdin, env)

    client, _ = items_server([])
    reset = ShopReset(
        COMPOSE, ITEMS, run=slow, http=client, sleep=no_sleep, timeout_s=60, clock=lambda: now[0]
    )
    with pytest.raises(EvalError, match="not back after 60 s"):
        await reset()
    assert sum(argv[1] == "inspect" for argv, _ in docker.calls) <= 3


def test_the_reset_waits_on_the_shops_item_url(tmp_path):
    shipped = eval_host(settings(tmp_path, hosts_file=REPO / "config" / "hosts.demo.yaml"))
    assert shop_items_url(shipped) == "http://localhost:8080/api/items"
    with pytest.raises(EvalError, match="--no-reset"):
        shop_items_url(eval_host(settings(tmp_path)))


def test_a_later_batch_of_other_scenarios_keeps_the_earlier_ones_in_the_report(tmp_path):
    """#35 ran one scenario per batch with --only and --append; the report must list all."""
    hung = SCENARIO.model_copy(update={"id": "hung", "fault": "hung"})
    raw = tmp_path / "scenarios-20261103-142124.jsonl"
    assert suite_scenarios(raw, [SCENARIO]) == [SCENARIO]  # a new suite: this batch's own
    started = datetime(2026, 11, 3, 14, 21, 24, tzinfo=UTC)
    info = RunInfo(
        started_at=started, finished_at=started, model="tiny-local", provider="local",
        judge="manual", repeat=1, scenarios=[SCENARIO], command="x", raw=str(raw),
    )  # fmt: skip
    write_info(raw, info)
    assert suite_scenarios(raw, [hung]) == [SCENARIO, hung]
    assert suite_scenarios(raw, [hung, SCENARIO]) == [SCENARIO, hung]


async def test_a_scenario_with_a_github_fixture_gets_the_github_tools(tmp_path):
    fake = engine()
    model = FakeChatModel(script=script(), structured_output_unsupported=True)
    with_repo = HOSTS + "    services:\n      shop:\n        repo: DannyKuk/bleep\n"
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    plain = await run_scenario(
        SCENARIO, settings(tmp_path / "a", with_repo), faults=FakeFaults(fake),
        judge=FakeJudge(), connector=connector(fake), models=Models(model),
    )  # fmt: skip
    assert plain.error is None
    assert not {t.name for t in model.bound_tools} & {"recent_commits", "commit_diff"}

    fake = engine()
    model = FakeChatModel(script=script(), structured_output_unsupported=True)
    served = SCENARIO.model_copy(update={"github": "bad-deploy"})
    result = await run_scenario(
        served, settings(tmp_path / "b", with_repo), faults=FakeFaults(fake),
        judge=FakeJudge(), connector=connector(fake), models=Models(model),
    )  # fmt: skip
    assert result.error is None
    assert {t.name for t in model.bound_tools} >= {"recent_commits", "commit_diff"}


async def test_the_suite_offers_knowledge_search_over_the_snapshot_when_given_an_index(tmp_path):
    from bleep.memory.embeddings import FakeEmbedder
    from evals.run import snapshot_index

    folder = tmp_path / "knowledge"
    folder.mkdir()
    index = await snapshot_index(folder, FakeEmbedder())
    model = FakeChatModel(script=script(), structured_output_unsupported=True)
    faults = FakeFaults(engine())

    async def connect(_host: HostConfig):
        return None, faults.engine

    async def no_sleep(_seconds: float) -> None:
        return None

    await run_suite(
        [SCENARIO], settings(tmp_path), 1, faults=faults, judge=FakeJudge(), connector=connect,
        models=Models(model), sleep=no_sleep, knowledge=index,
    )  # fmt: skip

    assert "knowledge_search" in {t.name for t in model.bound_tools}
    assert len(await index.document_ids("docker:")) >= 20


def test_the_reproduce_command_keeps_the_knowledge_switch():
    from evals.run import _command, _parser

    assert "--no-knowledge" in _command(_parser().parse_args(["--no-knowledge"]))
    assert "--no-knowledge" not in _command(_parser().parse_args([]))


async def test_a_scenario_with_an_mcp_fixture_serves_it_and_proposes_with_its_act_tool(tmp_path):
    """The MCP plugin on the fixture's server: Investigate gets its tools, Propose is told how to
    write an mcp.call, and the plan passes the plan check; nothing is called or executed."""
    fake = engine()
    served = SCENARIO.model_copy(
        update={
            "mcp": "caddy",
            "expected": SCENARIO.expected.model_copy(
                update={
                    "action": ExpectedAction(
                        kind="act",
                        steps=[
                            ExpectedStep(action="mcp.call", target="shop-web", tool="caddy_reload")
                        ],
                    ),
                }
            ),
        }
    )
    turns = script()
    step = {
        "action": "mcp.call",
        "host_id": "demo",
        "target": "shop-web",
        "params": {"tool": "caddy_reload", "arguments": '{"container": "shop-web"}'},
    }
    plan = {"kind": "act", "steps": [step], "reason": "reload it", "verification": {}}
    turns[-1] = reply(json.dumps(plan), tokens_in=2800)
    model = FakeChatModel(script=turns, structured_output_unsupported=True)

    result = await run_scenario(
        served, settings(tmp_path), faults=FakeFaults(fake), judge=FakeJudge(),
        connector=connector(fake), models=Models(model),
    )  # fmt: skip

    assert result.error is None and fake.acts == []
    assert result.action_correct and not result.unnecessary_action
    assert {"mcp_tools", "mcp_call"} <= {t.name for t in model.bound_tools}
    propose_prompt = "\n".join(m.text for m in model.seen[-1])
    assert "- `mcp.call`: run one act tool" in propose_prompt
    assert "The MCP plugin's act tools: `caddy_reload`, `caddy_purge_cache`" in propose_prompt
    assert '<untrusted source="mcp"' in propose_prompt


async def test_without_an_mcp_fixture_the_plugin_stays_off(tmp_path):
    fake = engine()
    model = FakeChatModel(script=script(), structured_output_unsupported=True)

    result = await run_scenario(
        SCENARIO, settings(tmp_path), faults=FakeFaults(fake), judge=FakeJudge(),
        connector=connector(fake), models=Models(model),
    )  # fmt: skip

    assert result.error is None
    assert not {"mcp_tools", "mcp_call"} & {t.name for t in model.bound_tools}
    assert "- `mcp.call`" not in "\n".join(m.text for m in model.seen[-1])
