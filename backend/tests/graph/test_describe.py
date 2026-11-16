"""The graph nodes describe a diagnosis, containers and lists one way, in words from prompt parts
(#623)."""

import re
from datetime import UTC, datetime
from pathlib import Path

from bleep import prompts
from bleep.graph import describe
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.graph import ContainerMemory, Diagnosis, Evidence, GatherContext

T0 = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
GRAPH = Path(describe.__file__).parent
PARTS = prompts.PROMPTS_DIR / "graph" / "parts"


def container(name: str, state: str = "running", exit_code: int | None = None):
    return ContainerSummary(
        id=name, name=name, image="img:1", state=state, exit_code=exit_code, restart_count=2
    )


def test_a_list_is_one_item_a_line_and_none_when_empty():
    assert describe.bullets(["a", "b"]) == "- a\n- b"
    assert describe.bullets([]) == "(none)"


def test_a_diagnosis_names_its_cause_confidence_services_and_evidence():
    found = Diagnosis(
        root_cause="the db is down",
        category="dependency",
        confidence=0.856,
        affected_services=["shop", "billing"],
        evidence=[Evidence(source="logs", detail="connection refused")],
    )

    assert describe.diagnosis(found) == (
        "Root cause: the db is down\n"
        "Category: dependency\n"
        "Confidence: 0.86\n"
        "Affected services: shop, billing\n"
        "- logs: connection refused"
    )
    assert describe.diagnosis(None) == "(none)"


def test_a_diagnosis_without_services_or_evidence_has_no_empty_lines():
    found = Diagnosis(root_cause="unclear", category="unknown", confidence=0.2)

    assert describe.diagnosis(found) == "Root cause: unclear\nCategory: unknown\nConfidence: 0.20"


def test_a_container_carries_its_exit_code_only_when_it_has_one():
    context = GatherContext(
        at=T0, containers=[container("web-1", "exited", 137), container("db-1")]
    )

    assert describe.containers(context) == (
        "- web-1: exited (exit 137), health none, restarts 2, image img:1\n"
        "- db-1: running, health none, restarts 2, image img:1"
    )
    assert describe.containers(GatherContext(at=T0)) == "(none)"
    assert describe.containers(None) == "(none)"


def test_the_top_memory_users_show_their_limit_only_when_the_engine_reports_one():
    used = [
        ContainerMemory(name="a", mem_usage=2**30, mem_limit=2**31, mem_percent=50.4),
        ContainerMemory(name="b", mem_usage=2**29, mem_limit=0, mem_percent=12.0),
    ]

    assert describe.top_memory(GatherContext(at=T0, top_memory=used)) == (
        "Containers using the most memory:\n- a: 1.0 GiB of 2.0 GiB (50%)\n- b: 0.5 GiB (12%)"
    )
    assert describe.top_memory(GatherContext(at=T0)) == ""


def test_a_step_names_its_params_only_when_it_has_some():
    assert describe.step("docker.restart", "web-1", "a") == "docker.restart web-1 on a"
    assert describe.step("coolify.set_env", "shop", "a", "key=X") == (
        "coolify.set_env shop on a (key=X)"
    )


def test_every_part_is_used_by_the_graph():
    code = "\n".join(p.read_text(encoding="utf-8") for p in GRAPH.rglob("*.py"))
    used = set(re.findall(r'part\(\s*"([a-z_]+)"', code))
    files = {p.stem for p in PARTS.glob("*.md")}
    assert files - used == set()


def test_no_part_can_be_overridden_from_the_dev_panel():
    names = [f"graph/parts/{p.stem}" for p in PARTS.glob("*.md")]
    assert names
    assert not set(names) & set(prompts.overridable_names())
