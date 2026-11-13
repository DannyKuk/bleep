"""A scenario on a scripted host with a Coolify fixture: how it loads, what it expects, and that
the key that breaks its app is named by the log alone (#359)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from pydantic import SecretStr

from bleep.hosts.registry import Registry
from bleep.plugins.coolify.client import CoolifyClient
from bleep.schemas.graph import Proposal
from evals.coolify import TOKEN, URL, load_coolify_fixture, serve
from evals.report import RunInfo, render
from evals.run import _scripted_connector
from evals.scenarios import SCENARIOS_DIR, ScenarioError, ScenarioResult, load_scenario

GOOD = {
    "id": "coolify-set-env",
    "subject": {
        "kind": "service",
        "host_id": "demo",
        "service_id": "demo/store0uuid0000000000000a1",
    },
    "expected": {
        "root_cause": "SESSION_TTL_S is not set.",
        "category": "config",
        "action": {"kind": "no_action"},
    },
    "scripted": {
        "project": "storefront",
        "signals": [{"kind": "container_died", "container": "storefront-web"}],
        "containers": [{"name": "storefront-web", "logs": "FATAL SESSION_TTL_S is not set\n"}],
    },
    "coolify": "storefront",
}


UNKNOWN_SIGNAL = GOOD["scripted"] | {"signals": [{"kind": "oom", "container": "x"}]}


def write(directory: Path, body: dict) -> Path:
    path = directory / "coolify-set-env.yaml"
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    return path


def test_a_scripted_scenario_opens_from_its_signals_and_switches_no_fault(tmp_path):
    scenario = load_scenario(write(tmp_path, GOOD))
    assert scenario.fault is None and not scenario.look
    assert scenario.opener == "the signals of a scripted host"
    assert scenario.scripted.config("demo").services.keys() == {"storefront"}


def without(*names: str) -> dict:
    return {k: v for k, v in GOOD.items() if k not in names}


@pytest.mark.parametrize(
    ("body", "why"),
    [
        (GOOD | {"fault": "db-down"}, "one of the three"),
        (GOOD | {"look": True}, "one of the three"),
        (without("scripted") | {"fault": "hung"}, "labels a container of a scripted host"),
        (GOOD | {"mcp": "caddy"}, "not both"),
        (GOOD | {"coolify": "nope"}, "nope.yaml"),
        (
            GOOD
            | {
                "scripted": GOOD["scripted"]
                | {
                    "containers": [{"name": "storefront-db"}],
                    "signals": [{"kind": "oom", "container": "storefront-db"}],
                }
            },
            "not in the scripted host",
        ),
        (GOOD | {"scripted": UNKNOWN_SIGNAL}, "containers the host does not have"),
    ],
)
def test_a_scripted_or_coolify_scenario_that_does_not_fit_is_refused(tmp_path, body, why):
    with pytest.raises(ScenarioError, match=why):
        load_scenario(write(tmp_path, body))


def test_the_shipped_scenario_expects_set_env_with_its_key_and_then_a_redeploy_of_the_app():
    scenario = load_scenario(SCENARIOS_DIR / "coolify-set-env.yaml")
    fixture = load_coolify_fixture(scenario.coolify)
    steps = scenario.expected.action.steps
    assert scenario.expected.action.ordered
    assert [(s.action, s.target) for s in steps] == [
        ("coolify.set_env", fixture.name),
        ("coolify.redeploy", fixture.name),
    ]
    assert steps[0].params == {"key": "SESSION_TTL_S"}
    assert scenario.expected.must_name == ["SESSION_TTL_S"]
    assert scenario.subject.service_id == f"{scenario.subject.host_id}/{fixture.uuid}"


def test_only_the_log_names_the_key_that_breaks_the_shipped_scenarios_app():
    """Not its environment, not Coolify's env keys, not its deployments: the key is in the failing
    container's log alone."""
    scenario = load_scenario(SCENARIOS_DIR / "coolify-set-env.yaml")
    key = scenario.expected.must_name[0]
    fixture = load_coolify_fixture(scenario.coolify)
    containers = {c.name: c for c in scenario.scripted.containers}
    assert key in containers[fixture.container].logs
    assert not any(key in text for c in containers.values() for text in c.env)
    assert key not in fixture.env_keys
    assert key not in str(fixture.application) + str(fixture.deployments)


async def test_the_subject_is_the_coolify_service_the_registry_makes_of_the_scripted_host():
    """A subject that names no service the host has would still open an incident, and the run
    would read an empty one."""
    scenario = load_scenario(SCENARIOS_DIR / "coolify-set-env.yaml")
    fixture = load_coolify_fixture(scenario.coolify)
    connector = _scripted_connector(scenario, serve(fixture))
    registry = Registry([scenario.scripted.config(scenario.subject.host_id)], connector)
    await registry.start()
    try:
        service = registry.service(scenario.subject.service_id)
    finally:
        await registry.close()

    assert service is not None and service.source == "coolify"
    assert [c.name for c in service.containers] == [fixture.container]


async def test_the_served_deployments_are_dated_from_when_the_scenario_runs():
    fixture = load_coolify_fixture("storefront")
    now = datetime(2027, 3, 1, 12, 0, tzinfo=UTC)
    client = CoolifyClient(URL, SecretStr(TOKEN), transport=serve(fixture, lambda: now))

    newest, older = await client.deployments(fixture.uuid)

    assert newest.created_at == "2027-03-01T11:40:00.000000Z"
    assert newest.finished_at == "2027-03-01T11:43:00.000000Z"
    assert older.created_at == "2027-02-27T10:00:00.000000Z"


def test_a_report_says_a_scripted_scenario_ran_without_a_fault_and_what_it_expects():
    scenario = load_scenario(SCENARIOS_DIR / "coolify-set-env.yaml")
    info = RunInfo(
        started_at=datetime(2026, 11, 13, 9, 0, tzinfo=UTC),
        finished_at=datetime(2026, 11, 13, 9, 5, tzinfo=UTC),
        model="m",
        provider="openrouter",
        judge="j",
        repeat=1,
        scenarios=[scenario],
        command="python -m evals.run",
        raw="raw.jsonl",
    )
    run = ScenarioResult(id=scenario.id, run=1, model="m", judge="j")
    run.proposal = Proposal(kind="no_action", reason="r", human_instructions="h")

    text = render(info, [run])

    assert "diagnosed and proposed for 1 scripted host," in text
    assert "demo fault" not in text
    assert "- Opened by the signals of a scripted host" in text
    assert 'coolify.set_env storefront with params {"key": "SESSION_TTL_S"}' in text
    assert "A scripted host switches no fault and touches no Docker engine" in text
    assert "Each run opens the incident from the scenario's scripted signals" in text
    assert "switches the fault on" not in text and "from the incident opening" in text
