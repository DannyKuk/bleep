"""A scenario on a scripted host with a Coolify fixture, run on fakes: the incident opens from the
scripted signals with no fault and no Docker engine, the Coolify plugin is on against the served
API, the plan Propose writes passes the plan check and is scored, and nothing is ever written to
Coolify (#359)."""

import json
from typing import Literal

import pytest
from langchain_core.messages import AIMessage
from pydantic import SecretStr
from test_eval_run import HOSTS, SCENARIO, URLS, FakeJudge, Models, reply, settings

import evals.run as run_module
from bleep.hosts.config import HostConfig
from bleep.plugins.coolify.client import CoolifyClient, CoolifyError
from evals.coolify import TOKEN, URL, load_coolify_fixture, serve
from evals.run import REPO, main, run_scenario, run_suite
from evals.scenarios import SCENARIOS_DIR, Scenario, ScenarioResult, load_scenario
from fakes import FakeChatModel

KEY = "SESSION_TTL_S"


class NoFaults:
    """A fault script a scripted scenario must never reach."""

    async def switch(self, fault: str, state: Literal["on", "off"]) -> None:
        raise AssertionError(f"switched {fault} {state}")


async def no_docker(_host: HostConfig):
    raise AssertionError("a scripted scenario reached for a Docker engine")


def scenario() -> Scenario:
    return load_scenario(SCENARIOS_DIR / "coolify-set-env.yaml")


def turns(diagnosis_text: str, plan_steps: list[dict]) -> list[AIMessage]:
    logs = {"name": "container_logs", "args": {"container": "storefront-web"}, "id": "call-1"}
    keys = {"name": "coolify_env_keys", "args": {"app": "storefront"}, "id": "call-2"}
    diagnosis = {"root_cause": diagnosis_text, "category": "config", "confidence": 0.9}
    plan = {
        "kind": "act",
        "steps": plan_steps,
        "reason": "set the missing setting",
        "verification": {},
    }
    return [
        reply(tool_calls=[logs, keys], tokens_in=1200),
        reply(f"{KEY} is missing", tokens_in=2500),
        reply(json.dumps(diagnosis), tokens_in=3000),
        reply(json.dumps(plan), tokens_in=2800),
    ]


def set_env(key: str = KEY) -> dict:
    params = {"key": key, "value": "3600"}
    return {
        "action": "coolify.set_env",
        "host_id": "demo",
        "target": "storefront",
        "params": params,
    }


REDEPLOY = {"action": "coolify.redeploy", "host_id": "demo", "target": "storefront", "params": {}}
CAUSE = f"storefront-web exits at startup: {KEY} is not set in Coolify"


async def run(model: FakeChatModel, tmp_path, **kwargs):
    return await run_scenario(
        scenario(),
        settings(tmp_path),
        faults=NoFaults(),
        judge=FakeJudge(),
        connector=no_docker,
        models=Models(model),
        **kwargs,
    )


async def test_a_run_opens_from_the_scripted_signals_and_plans_set_env_then_redeploy(tmp_path):
    model = FakeChatModel(
        script=turns(CAUSE, [set_env(), REDEPLOY]), structured_output_unsupported=True
    )
    served = serve(load_coolify_fixture("storefront"))

    result = await run(model, tmp_path, coolify=served)

    assert result.error is None and result.failure is None
    assert model.cursor == 4
    assert result.proposal is not None and result.proposal.refused_steps == []
    assert [s.action for s in result.proposal.steps] == ["coolify.set_env", "coolify.redeploy"]
    assert result.action_correct and not result.unnecessary_action and result.category_correct
    assert (result.diagnosis_unnamed, result.plan_unnamed) == ([], [])
    assert [c.name for c in result.tool_calls] == ["container_logs", "coolify_env_keys"]
    assert {"coolify_applications", "coolify_env_keys", "coolify_deployments"} <= {
        t.name for t in model.bound_tools
    }
    assert served.writes == []  # the harness stops after Propose
    first = "\n".join(m.text for m in model.seen[0])
    assert "storefront" in first
    # What the model reads of the log, after the guard's redaction: the key and its value.
    after_tools = "\n".join(m.text for m in model.seen[1])
    assert f"{KEY} is not set" in after_tools and f"{KEY}=3600" in after_tools
    propose = "\n".join(m.text for m in model.seen[-1])
    assert "- `coolify.set_env`" in propose


async def test_the_run_serves_the_fixture_itself_when_it_is_not_given_one(tmp_path):
    model = FakeChatModel(
        script=turns(CAUSE, [set_env(), REDEPLOY]), structured_output_unsupported=True
    )

    result = await run(model, tmp_path)

    assert result.error is None and result.action_correct


async def test_a_plan_that_sets_another_key_is_not_the_expected_plan(tmp_path):
    steps = [set_env("SESSION_TTL"), REDEPLOY]
    model = FakeChatModel(script=turns(CAUSE, steps), structured_output_unsupported=True)

    result = await run(model, tmp_path)

    assert result.error is None
    assert not result.action_correct and result.unnecessary_action
    assert result.plan_unnamed == [KEY]  # `SESSION_TTL` is not `SESSION_TTL_S`


async def test_a_diagnosis_that_drops_the_key_is_scored_as_not_naming_it(tmp_path):
    model = FakeChatModel(
        script=turns("storefront-web exits at startup", [set_env(), REDEPLOY]),
        structured_output_unsupported=True,
    )

    result = await run(model, tmp_path)

    assert result.diagnosis_unnamed == [KEY]
    assert result.plan_unnamed == []  # the key is in the step's params


async def test_a_set_env_without_its_redeploy_is_refused_by_the_plan_check_and_not_correct(
    tmp_path,
):
    script = turns(CAUSE, [set_env()])
    model = FakeChatModel(script=[*script, script[-1]], structured_output_unsupported=True)

    result = await run(model, tmp_path)

    assert result.error is None
    assert not result.action_correct  # Propose was asked again and wrote the same plan
    assert "coolify.redeploy" in "\n".join(m.text for m in model.seen[-1])


async def test_a_coolify_request_that_would_write_fails_the_run(tmp_path):
    model = FakeChatModel(
        script=turns(CAUSE, [set_env(), REDEPLOY]), structured_output_unsupported=True
    )
    served = serve(load_coolify_fixture("storefront"))
    client = CoolifyClient(URL, SecretStr(TOKEN), transport=served)

    async def execute(*_args) -> None:
        with pytest.raises(CoolifyError):
            await client.set_env(served.fixture.uuid, KEY, SecretStr("3600"))

    result = await run(model, tmp_path, coolify=served, after=execute)

    assert result.failure == "error"
    path = f"/api/v1/applications/{served.fixture.uuid}/envs"
    assert result.error == f"the Coolify fixture was asked to write: PATCH {path}"


async def test_a_suite_of_scripted_scenarios_resets_and_settles_nothing(tmp_path):
    resets, waits = [], []

    async def reset() -> None:
        resets.append(1)

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    model = FakeChatModel(
        script=turns(CAUSE, [set_env(), REDEPLOY]) * 2, structured_output_unsupported=True
    )
    results = await run_suite(
        [scenario()], settings(tmp_path), 2,
        faults=NoFaults(), judge=FakeJudge(), connector=no_docker,
        models=Models(model), reset=reset, sleep=sleep,
    )  # fmt: skip

    assert [r.run for r in results] == [1, 2] and all(r.error is None for r in results)
    assert resets == [] and waits == []


def patched_suite(monkeypatch, tmp_path):
    """`main` with its model, its suite and its reports replaced; what the suite was given."""
    given = {}

    async def suite(scenarios, _settings, _repeat, **kwargs):
        given["reset"] = kwargs["reset"]
        return []

    async def no_demo(_settings):
        raise AssertionError("a scripted scenario looked at the demo host's engine")

    monkeypatch.setattr(run_module, "run_suite", suite)
    monkeypatch.setattr(run_module, "demo_containers", no_demo)
    monkeypatch.setattr(run_module, "build_judge", lambda *_: FakeJudge())
    monkeypatch.setattr(run_module, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(run_module, "RAW_DIR", tmp_path / "raw")
    return given


def test_the_command_for_a_scripted_scenario_leaves_the_demo_stack_alone(tmp_path, monkeypatch):
    """It needs no fault script, takes no stack lock, never lists the demo host's containers and
    resets no shop (#359)."""
    given = patched_suite(monkeypatch, tmp_path)
    scripted = settings(tmp_path, fault_script=tmp_path / "never.sh")

    code = main(["--only", "coolify-set-env", "--no-knowledge"], settings=scripted)

    assert code == 0 and given["reset"] is None
    assert not scripted.stack_lock.exists()


def test_the_command_for_a_demo_fault_still_needs_the_fault_script_and_the_lock(
    tmp_path, monkeypatch, capsys
):
    patched_suite(monkeypatch, tmp_path)
    scripted = settings(tmp_path, fault_script=tmp_path / "never.sh")

    assert main(["--only", "db-down"], settings=scripted) == 2
    assert "no fault script" in capsys.readouterr().err


def stub_run_scenario(monkeypatch) -> list[tuple[str, object]]:
    """`run_suite` with `run_scenario` replaced: (scenario id, its knowledge) in the order run."""
    ran: list[tuple[str, object]] = []

    async def fake(scenario, _settings, *, run, knowledge=None, **_kwargs):
        ran.append((scenario.id, knowledge))
        return ScenarioResult(id=scenario.id, run=run, model="m", judge="j")

    monkeypatch.setattr(run_module, "run_scenario", fake)
    return ran


def demo_scenario() -> Scenario:
    return SCENARIO


async def mixed_suite(tmp_path, order: list[Scenario], reset=None, **kwargs):
    events: list[str] = []

    async def sleep(seconds: float) -> None:
        events.append("settle")

    async def reset_shop() -> None:
        events.append("reset")

    results = await run_suite(
        order, settings(tmp_path), 1, faults=NoFaults(), judge=FakeJudge(),
        sleep=sleep, reset=reset_shop if reset else None, **kwargs,
    )  # fmt: skip
    return events, results


async def test_a_mixed_suite_resets_before_every_demo_run_and_never_for_a_scripted_one(
    tmp_path, monkeypatch
):
    ran = stub_run_scenario(monkeypatch)
    scripted, demo = scenario(), demo_scenario()

    events, _ = await mixed_suite(tmp_path, [scripted, demo, scripted, demo], reset=True)

    assert [r[0] for r in ran] == [scripted.id, demo.id, scripted.id, demo.id]
    assert events == ["reset", "reset"]  # the first scripted run leaves the shop as it is


async def test_a_mixed_suite_settles_only_after_a_run_that_touched_the_shop(tmp_path, monkeypatch):
    stub_run_scenario(monkeypatch)
    scripted, demo = scenario(), demo_scenario()

    # A scripted run first, or between demo runs, disturbs nothing; the wait comes only before a
    # demo run that follows one that did touch the shop.
    first, _ = await mixed_suite(tmp_path, [scripted, demo])
    second, _ = await mixed_suite(tmp_path, [demo, scripted, demo])
    third, _ = await mixed_suite(tmp_path, [demo, demo, scripted])
    fourth, _ = await mixed_suite(tmp_path, [scripted, scripted])

    assert (first, second, third, fourth) == ([], ["settle"], ["settle"], [])


async def test_a_scripted_run_gets_its_own_knowledge_and_a_demo_run_the_shops(
    tmp_path, monkeypatch
):
    ran = stub_run_scenario(monkeypatch)
    shop_docs, docker_docs = object(), object()

    await mixed_suite(
        tmp_path, [demo_scenario(), scenario()],
        knowledge=shop_docs, scripted_knowledge=docker_docs,
    )  # fmt: skip

    assert ran == [(demo_scenario().id, shop_docs), (scenario().id, docker_docs)]


def patched_command(monkeypatch, tmp_path) -> dict:
    """`main` with the suite, the engine check and the embedding replaced: what the suite was
    given, and the repo docs each index was built with."""
    given: dict = {"indexes": []}

    async def suite(_scenarios, _settings, _repeat, **kwargs):
        given.update(knowledge=kwargs["knowledge"], scripted=kwargs["scripted_knowledge"])
        return []

    async def index(folder, _embedder, repo_docs=()):
        given["indexes"].append((folder.name, [d.title for d in repo_docs]))
        return folder.name

    async def containers(_settings):
        return []

    monkeypatch.setattr(run_module, "run_suite", suite)
    monkeypatch.setattr(run_module, "snapshot_index", index)
    monkeypatch.setattr(run_module, "demo_containers", containers)
    monkeypatch.setattr(run_module, "FastEmbedEmbedder", lambda: None)
    monkeypatch.setattr(run_module, "checkout_repo_docs", lambda _host: [Doc("shop docs")])
    monkeypatch.setattr(run_module, "build_judge", lambda *_: FakeJudge())
    monkeypatch.setattr(run_module, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(run_module, "RAW_DIR", tmp_path / "raw")
    return given


class Doc:
    def __init__(self, title: str) -> None:
        self.title = title


def test_a_scripted_only_command_searches_the_docker_docs_alone(tmp_path, monkeypatch):
    given = patched_command(monkeypatch, tmp_path)
    scripted = settings(tmp_path, fault_script=tmp_path / "never.sh")

    assert main(["--only", "coolify-set-env"], settings=scripted) == 0

    assert given["indexes"] == [("scripted", [])]
    assert given["knowledge"] is None and given["scripted"] == "scripted"
    info = next((tmp_path / "raw").glob("*.info.json")).read_text(encoding="utf-8")
    assert '"repo_docs":[]' in info.replace(" ", "")


def test_a_mixed_command_gives_each_its_own_index_and_the_report_says_which(tmp_path, monkeypatch):
    given = patched_command(monkeypatch, tmp_path)
    hosts = HOSTS + URLS
    mixed = settings(tmp_path, hosts, fault_script=REPO / "demo" / "fault.sh")

    assert main(["--only", "coolify-set-env,db-down"], settings=mixed) == 0

    assert given["indexes"] == [("demo", ["shop docs"]), ("scripted", [])]
    assert given["knowledge"] == "demo" and given["scripted"] == "scripted"
    report = next((tmp_path / "reports").glob("*.md")).read_text(encoding="utf-8")
    assert "shop docs" in report and "for the demo faults (a scripted host links no repo)" in report
    assert "recreated before every demo-fault run" in report
