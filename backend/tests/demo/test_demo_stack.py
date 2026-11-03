"""The demo stack's files agree with each other and with Bleep's guard (docs/demo.md).

No Docker here: these read docker-compose.yaml, demo/compose.yaml and config/hosts.demo.yaml.
"""

import ast
from pathlib import Path

import httpx
import pytest
import yaml

from bleep.graph.deps import CORE_ACTIONS
from bleep.guard.plan_check import check_plan
from bleep.hosts.config import load_hosts_file
from bleep.hosts.discovery import COMPOSE_PROJECT, SELF_LABEL, group_services
from bleep.schemas.graph import PlanStep, Proposal, VerificationPlan
from fakes import FakeEngine
from graph_env import box, graph_env

REPO = Path(__file__).resolve().parents[3]
ROOT = yaml.safe_load((REPO / "docker-compose.yaml").read_text(encoding="utf-8"))
DEMO = yaml.safe_load((REPO / "demo" / "compose.yaml").read_text(encoding="utf-8"))
HOSTS = load_hosts_file(REPO / "config" / "hosts.demo.yaml")
SHOP = ("shop-web", "shop-db", "shop-proxy", "shop-worker")


def shop_containers(**states: dict) -> list:
    """The demo shop as the engine lists it, beside Bleep's own containers. `states` gives a
    container's state and health as a fault leaves them; the rest run with no health check."""
    own = {COMPOSE_PROJECT: "bleep", SELF_LABEL: "true"}
    shop = [box(name, DEMO["name"], **states.get(name, {})) for name in SHOP]
    return shop + [box("bleep-backend-1", labels=own), box("bleep-socket-proxy-1", labels=own)]


def test_the_shop_is_its_own_compose_project():
    assert DEMO["name"] == "shop"
    assert "include" not in ROOT
    # The demo profile on the root file is the socket proxy alone.
    assert ROOT["services"]["socket-proxy"]["profiles"] == ["demo"]
    assert set(DEMO["services"]) == set(SHOP)
    for name, service in DEMO["services"].items():
        # One command starts the whole shop.
        assert "profiles" not in service, name
        # docs/demo.md, fault.sh and the expected plans name the containers so.
        assert service["container_name"] == name


def test_the_shop_runs_on_a_remote_engine():
    # Bind mounts name paths on the machine running compose, which a remote engine does not have:
    # every file the shop needs is built into its images, and its volumes are named ones.
    for name, service in DEMO["services"].items():
        for volume in service.get("volumes", []):
            assert volume.split(":")[0] in DEMO["volumes"], (name, volume)


def test_the_backend_reaches_the_shops_published_port():
    assert "host.docker.internal:host-gateway" in ROOT["services"]["backend"]["extra_hosts"]


def test_the_shop_has_the_limits_its_faults_need():
    services = DEMO["services"]
    assert services["shop-worker"]["mem_limit"] == "64m"
    assert services["shop-worker"]["restart"] == "unless-stopped"
    assert services["shop-proxy"]["ports"] == ["8080:8080"]
    assert "uploads:/uploads" in services["shop-web"]["volumes"]
    # The worker keeps the volume mounted, so a restart of shop-web does not empty it.
    assert "uploads:/uploads:ro" in services["shop-worker"]["volumes"]
    assert DEMO["volumes"]["uploads"]["driver_opts"] == {
        "type": "tmpfs",
        "device": "tmpfs",
        "o": "size=20m",
    }


def test_the_demo_hosts_file_describes_the_shop_service():
    (host,) = HOSTS.hosts
    # Bleep's own containers are left out, so the shop is the only service.
    (service,) = group_services(host.id, shop_containers(), host.services)
    assert (service.id, service.name) == ("demo/compose:shop", "shop")
    assert sorted(c.name for c in service.containers) == sorted(SHOP)
    assert {(httpx.URL(u.url).host, httpx.URL(u.url).port) for u in service.urls} == {
        ("host.docker.internal", 8080)
    }
    assert service.urls[0].body_contains == '"status":"ok"'
    assert service.urls[1].body_contains == "Coffee beans"
    # shop-db is guarded by a rule, not by protected_containers: starting it is the db-down fix.
    assert service.protected == set()
    assert any("shop-db" in rule for rule in service.rules)


# Each fault's containers as it leaves them at Propose, and the container its signal names
# (docs/demo.md): the stopped shop-db is no signal of its own.
UNHEALTHY = {"state": "running", "health": "unhealthy"}
FAULTS = {
    "db-down": (
        {"shop-db": {"state": "exited", "exit_code": 0}, "shop-web": UNHEALTHY},
        "shop-web",
    ),
    "hung": ({"shop-web": UNHEALTHY}, "shop-web"),
}


@pytest.mark.parametrize(
    ("fault", "action", "target", "verify"),
    [
        ("db-down", "docker.start", "shop-db", ["shop-db", "shop-web"]),
        ("hung", "docker.restart", "shop-web", ["shop-web"]),
    ],
)
async def test_the_plans_docs_demo_expects_pass_the_plan_check(
    tmp_path, fault, action, target, verify
):
    (host,) = HOSTS.hosts
    states, signalled = FAULTS[fault]
    engine = FakeEngine(host.id, shop_containers(**states))
    env = await graph_env(tmp_path, engine=engine, host=host)
    urls = [u.url for u in host.services["shop"].urls]
    proposal = Proposal(
        kind="act",
        steps=[PlanStep(action=action, host_id=host.id, target=target)],
        reason="docs/demo.md",
        verification=VerificationPlan(containers=verify, urls=urls),
    )
    found = check_plan(
        proposal, env.registry, set(CORE_ACTIONS), host.id, incident_containers=[signalled]
    )
    assert found == []


def test_the_hung_fault_freezes_without_saying_so():
    """A real hang does not announce itself, so /freeze writes no line before it blocks (#106)."""
    tree = ast.parse((REPO / "demo" / "shop" / "app.py").read_text(encoding="utf-8"))
    (freeze,) = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
                 and n.name == "freeze"]  # fmt: skip
    body = [n for stmt in freeze.body for n in ast.walk(stmt)]  # not its @app.post decorator
    assert [ast.unparse(n.func) for n in body if isinstance(n, ast.Call)] == ["time.sleep"]


def test_the_fault_script_runs_compose_on_the_shop_project_alone():
    """COMPOSE_FILE and COMPOSE_PROJECT_NAME are Compose's own variables: one in the caller's
    environment must not point fault.sh at another project."""
    script = (REPO / "demo" / "fault.sh").read_text(encoding="utf-8")
    assert 'compose() { docker compose -p shop -f "$SHOP_COMPOSE" "$@"; }' in script
    assert "COMPOSE_FILE" not in script
