"""Scenario files: the shipped ones load, and a malformed one is refused with its file named."""

from pathlib import Path

import pytest
import yaml

from evals.scenarios import SCENARIOS_DIR, ScenarioError, load_scenario, load_scenarios

GOOD = {
    "id": "db-down",
    "fault": "db-down",
    "subject": {"kind": "service", "host_id": "demo", "service_id": "demo/compose:bleep"},
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
    assert scenario.subject.key == "service:demo:demo/compose:bleep"
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
