"""Scenario files: the shipped ones load, and a malformed one is refused with its file named."""

from pathlib import Path

import pytest
import yaml

from bleep.hosts.config import load_hosts_file
from evals.mcp_server import load_mcp_fixture
from evals.run import checkout_repo_docs
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


def test_a_look_scenario_opens_by_a_look_and_switches_no_fault(tmp_path):
    body = {k: v for k, v in GOOD.items() if k != "fault"} | {"id": "look", "look": True}
    scenario = load_scenario(write(tmp_path, "look", body))
    assert scenario.fault is None and scenario.look
    assert scenario.opener == "a look on request"
    assert load_scenario(write(tmp_path, "db-down", GOOD)).opener == "the fault `db-down`"


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
        (changed(must_name=[""]), "must_name"),
        (changed(must_name=["DATABASE_URL "]), "must_name"),
        (changed(must_set={"WORKER_BATCH_MB": ""}), "must_set"),
        (changed(must_set={" WORKER_BATCH_MB": "8"}), "must_set"),
        ({k: v for k, v in GOOD.items() if k != "fault"}, "a fault or asks for a look"),
        (GOOD | {"look": True}, "a fault or asks for a look"),
        ({k: v for k, v in GOOD.items() if k != "fault"}
         | {"look": True, "subject": {"kind": "host", "host_id": "demo"}}, "never a whole host"),
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
    """Each scenario switches a fault fault.sh knows or asks for a look, opens on a service of
    the demo hosts file (the shop's own Compose project), and names the shop's containers as
    Compose names them."""
    compose = yaml.safe_load((REPO / "demo" / "compose.yaml").read_text(encoding="utf-8"))
    containers = {s["container_name"] for s in compose["services"].values()}
    hosts = yaml.safe_load((REPO / "config" / "hosts.demo.yaml").read_text(encoding="utf-8"))
    services = {
        f"{h['id']}/compose:{name}": h["id"] for h in hosts["hosts"] for name in h["services"]
    }
    assert compose["name"] == "shop" and "demo/compose:shop" in services
    for scenario in load_scenarios():
        assert scenario.look or scenario.fault in _demo_faults(), scenario.id
        assert services.get(scenario.subject.service_id) == scenario.subject.host_id, scenario.id
        assert {t for _, t, _ in scenario.expected.action.keys} <= containers, scenario.id


def test_what_a_shipped_scenario_must_name_is_a_key_or_container_of_the_demo_shop():
    """A `must_name` that is no environment key the shop's Compose file sets and no container of
    it could never be named from what Bleep reads, so it would fail every run (#357)."""
    compose = yaml.safe_load((REPO / "demo" / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"].values()
    known = {s["container_name"] for s in services}
    known |= {key for s in services for key in s.get("environment", {})}
    named = {s.id: s.expected.must_name for s in load_scenarios() if s.expected.must_name}
    assert named == {"bad-env": ["DATABASE_URL"], "oom": ["WORKER_BATCH_MB"]}
    for scenario_id, names in named.items():
        assert set(names) <= known, scenario_id


def test_what_a_shipped_scenario_must_set_is_what_the_shops_repo_docs_give():
    """A `must_set` value is the one the shop's linked docs give the key, which knowledge search
    holds in a scenario run as in the app; inspect hides the running value (#364)."""
    host = next(h for h in load_hosts_file(REPO / "config" / "hosts.demo.yaml").hosts)
    docs = "\n".join(d.text for d in checkout_repo_docs(host))
    wanted = {s.id: s.expected.must_set for s in load_scenarios() if s.expected.must_set}
    assert wanted == {"oom": {"WORKER_BATCH_MB": "8"}}
    for scenario_id, values in wanted.items():
        for key, value in values.items():
            row = next(line for line in docs.splitlines() if line.startswith(f"| `{key}`"))
            assert row.split(" | ")[2] == f"`{value}`", scenario_id


def test_a_scenario_with_an_mcp_fixture_expects_the_tool_its_server_offers():
    scenario = load_scenario(SCENARIOS_DIR / "misroute-mcp.yaml")
    assert scenario.mcp == "caddy"
    assert scenario.expected.action.keys == [("mcp.call", "shop-proxy", "caddy_reload")]
    assert scenario.expected.action.steps[0].arguments == {"container": "shop-proxy"}
    fixture = load_mcp_fixture(scenario.mcp)
    assert "caddy_reload" in fixture.act_tools
