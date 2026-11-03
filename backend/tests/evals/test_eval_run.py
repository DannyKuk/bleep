"""The harness on a fake engine, a scripted model and a fake judge: a fault opens the scenario's
incident, the graph stops after Propose, the fault goes off whatever happens, and the run is
measured and scored."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest
from langchain_core.messages import AIMessage

from bleep.hosts.config import HostConfig
from bleep.schemas.docker import ContainerDetail, DockerEvent, PortBinding
from bleep.schemas.graph import Diagnosis
from bleep.schemas.settings import ModelSettings
from evals.judge import JudgeError, Judgement
from evals.run import (
    DemoFaults,
    EvalError,
    EvalSettings,
    FaultError,
    demo_containers,
    docker_endpoint,
    eval_host,
    live_backend,
    main,
    run_scenario,
    run_suite,
    summary,
)
from evals.scenarios import Scenario
from fakes import FakeChatModel, FakeEngine
from graph_env import box

SERVICE = "demo/compose:bleep"
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
      bleep:
        urls:
          - url: http://host.docker.internal:8080/api/items
          - url: http://shop-proxy:8080/health
          - url: http://shop-web:8000/health
          - url: https://shop.example.com/
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
            box("shop-web", "bleep", health="healthy"),
            box("shop-db", "bleep"),
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
    assert summary(result).startswith("db-down run 1: root cause 0.80, plan exact, 1 tool call,")


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


async def test_the_demo_host_is_reached_from_this_machine(tmp_path):
    proxy = box("shop-proxy", "bleep")
    published = [PortBinding(container_port="8080/tcp", host_ip="0.0.0.0", host_port="18080")]
    fake = FakeEngine(
        "demo",
        [proxy, box("shop-web", "bleep")],
        details={"shop-proxy": ContainerDetail(summary=proxy, ports=published)},
    )
    host = await eval_host(settings(tmp_path, HOSTS + URLS), connector(fake))
    assert host.endpoint == "unix:///fake.sock"
    assert host.rules == ["shop-db holds the shop's items"]
    assert [u.url for u in host.services["bleep"].urls] == [
        "http://localhost:8080/api/items",  # host.docker.internal is this machine
        "http://localhost:18080/health",  # shop-proxy's 8080 is published on 18080
        "http://shop-web:8000/health",  # not published: left as it is
        "https://shop.example.com/",
    ]
    assert fake.closed  # the engine was only borrowed


async def test_an_unknown_host_is_refused(tmp_path):
    with pytest.raises(EvalError, match="demo"):
        await eval_host(settings(tmp_path, host_id="nope"), connector(engine()))


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
    assert not live_backend([box("shop-web", "bleep")])


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


async def test_a_failing_fault_script_is_a_fault_error(tmp_path):
    script = tmp_path / "fault.sh"
    script.write_text("#!/bin/sh\necho 'no such fault' >&2\nexit 3\n", encoding="utf-8")
    os.chmod(script, 0o755)
    with pytest.raises(FaultError, match="no such fault"):
        await DemoFaults(script).switch("nope", "on")
