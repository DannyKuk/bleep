"""Scenario files: the shipped ones load, and a malformed one is refused with its file named."""

from pathlib import Path

import pytest
import yaml

from evals.mcp_server import load_mcp_fixture
from evals.scenarios import SCENARIOS_DIR, ScenarioError, load_scenario, load_scenarios

GOOD = {
    "id": "db-down",
    "fault": "db-down",
    "subject": {"kind": "service", "host_id": "demo", "service_id": "demo/compose:shop"},
    "expected": {
        "root_cause": "The database container shop-db is stopped.",
        "category": "dependency",
        "action": {"kind": "act", "steps": [{"action": "docker.start", "target": "shop-db"}]},
        "must_not": ["docker.restart"],
    },
    "timeout_s": 600,
}


def write(directory: Path, name: str, body: dict) -> Path:
    path = directory / f"{name}.yaml"
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    return path


def changed(**expected: object) -> dict:
    return GOOD | {"expected": GOOD["expected"] | expected}


def test_every_shipped_scenario_loads():
    scenarios = load_scenarios()
    assert scenarios, f"no scenario in {SCENARIOS_DIR}"
    for scenario in scenarios:
        assert (SCENARIOS_DIR / f"{scenario.id}.yaml").is_file()


def test_a_scenario_loads_with_its_defaults(tmp_path):
    body = {k: v for k, v in GOOD.items() if k != "timeout_s"}
    scenario = load_scenario(write(tmp_path, "db-down", body))
    assert scenario.subject.key == "service:demo:demo/compose:shop"
    assert scenario.expected.action.ordered is False
    assert scenario.timeout_s == 600


def test_the_id_must_be_the_file_name(tmp_path):
    with pytest.raises(ScenarioError, match="other.yaml"):
        load_scenario(write(tmp_path, "other", GOOD))


@pytest.mark.parametrize(
    ("body", "why"),
    [
        (GOOD | {"surprise": 1}, "surprise"),
        (changed(category="weather"), "category"),
        (changed(action={"kind": "no_action", "steps": [{"action": "docker.start",
                                                          "target": "x"}]}), "no steps"),
        (changed(action={"kind": "act", "steps": []}), "one to three"),
        (changed(action={"kind": "act", "steps": [{"action": "docker.start", "target": "x"}] * 4}),
         "one to three"),
        (changed(action={"kind": "act", "steps": [{"action": "docker.kill", "target": "x"}]}),
         "docker.kill"),
        (changed(must_not=["docker.start"]), "docker.start"),
        (changed(root_cause=""), "root_cause"),
        (GOOD | {"timeout_s": 0}, "timeout_s"),
        (changed(action={"kind": "act", "steps": [{"action": "mcp.call", "target": "x"}]}),
         "names its tool"),
        (changed(action={"kind": "act", "steps": [{"action": "docker.start", "target": "x",
                                                   "tool": "t"}]}), "names its tool"),
        (GOOD | {"mcp": "no-such-fixture"}, "no-such-fixture"),
    ],
)  # fmt: skip
def test_a_malformed_scenario_is_refused(tmp_path, body, why):
    with pytest.raises(ScenarioError, match=why):
        load_scenario(write(tmp_path, "db-down", body))


def test_a_file_that_is_not_yaml_is_refused(tmp_path):
    path = tmp_path / "db-down.yaml"
    path.write_text("id: [unclosed", encoding="utf-8")
    with pytest.raises(ScenarioError, match="db-down.yaml"):
        load_scenario(path)


def test_only_picks_one_scenario(tmp_path):
    write(tmp_path, "db-down", GOOD)
    write(tmp_path, "hung", GOOD | {"id": "hung", "fault": "hung"})
    assert [s.id for s in load_scenarios(tmp_path)] == ["db-down", "hung"]
    assert [s.id for s in load_scenarios(tmp_path, only="hung")] == ["hung"]


def test_an_unknown_only_names_the_known_scenarios(tmp_path):
    write(tmp_path, "db-down", GOOD)
    with pytest.raises(ScenarioError, match="db-down"):
        load_scenarios(tmp_path, only="nope")


REPO = SCENARIOS_DIR.parents[2]


def _demo_faults() -> set[str]:
    """The faults `demo/fault.sh` switches, from its `FAULTS=` line."""
    for line in (REPO / "demo" / "fault.sh").read_text(encoding="utf-8").splitlines():
        if line.startswith("FAULTS="):
            return set(line.partition("=")[2].strip('"').split())
    raise AssertionError("demo/fault.sh has no FAULTS= line")


def test_every_shipped_scenario_fits_the_demo_shop():
    """Each scenario switches a fault fault.sh knows, opens on a service of the demo hosts file
    (the shop's own Compose project), and names the shop's containers as Compose names them."""
    compose = yaml.safe_load((REPO / "demo" / "compose.yaml").read_text(encoding="utf-8"))
    containers = {s["container_name"] for s in compose["services"].values()}
    hosts = yaml.safe_load((REPO / "config" / "hosts.demo.yaml").read_text(encoding="utf-8"))
    services = {
        f"{h['id']}/compose:{name}": h["id"] for h in hosts["hosts"] for name in h["services"]
    }
    assert compose["name"] == "shop" and "demo/compose:shop" in services
    for scenario in load_scenarios():
        assert scenario.fault in _demo_faults(), scenario.id
        assert services.get(scenario.subject.service_id) == scenario.subject.host_id, scenario.id
        assert {t for _, t, _ in scenario.expected.action.keys} <= containers, scenario.id


def test_a_scenario_with_an_mcp_fixture_expects_the_tool_its_server_offers():
    scenario = load_scenario(SCENARIOS_DIR / "misroute-mcp.yaml")
    assert scenario.mcp == "caddy"
    assert scenario.expected.action.keys == [("mcp.call", "shop-proxy", "caddy_reload")]
    assert scenario.expected.action.steps[0].arguments == {"container": "shop-proxy"}
    fixture = load_mcp_fixture(scenario.mcp)
    assert "caddy_reload" in fixture.act_tools
