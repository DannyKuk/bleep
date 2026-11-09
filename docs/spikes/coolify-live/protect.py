"""Spike #337, step 4: a protected Coolify application is refused by the plan check, by name and
by uuid, on the real server.

Runs inside the backend container and calls no model and nothing that writes; it reads the
Coolify server's containers over SSH (read only), as the watcher does:

    docker exec -i bleep-backend-1 /app/.venv/bin/python - < docs/spikes/coolify-live/protect.py

It builds a registry of the hosts file's Coolify host three times: as it is, with the real
application (`SPIKE_PROTECT_NAME`, default `molecula`) protected by its name, and protected by
its uuid (`SPIKE_PROTECT_UUID`), the protection added in memory only. For each it runs
`check_plan` on plans that name the application (a redeploy by name and by uuid, a set_env and
redeploy, a restart of one of its containers) and on a redeploy of the test application as a
control, and prints one JSON document with the violations each plan got.
"""

import asyncio
import json
import os
import time
from pathlib import Path

from bleep.guard.plan_check import check_plan
from bleep.hosts.config import ServiceOverrides, load_hosts_file
from bleep.hosts.registry import Registry
from bleep.schemas.graph import PlanStep, Proposal

HOSTS_FILE = Path(os.environ.get("BLEEP_HOSTS_FILE", "/config/hosts.yaml"))
HOST = os.environ.get("SPIKE_HOST", "coolify")
NAME = os.environ.get("SPIKE_PROTECT_NAME", "molecula")
UUID = os.environ.get("SPIKE_PROTECT_UUID", "hfys2nogpz7wrhcoqxymnfkt")
TEST_APP = os.environ.get("SPIKE_APP", "bleep-testapp")
ENABLED = {"coolify.redeploy", "coolify.set_env", "docker.restart", "docker.start", "docker.stop"}


def plan(*steps: tuple[str, str, dict[str, str]]) -> Proposal:
    return Proposal(
        kind="act",
        steps=[PlanStep(action=a, host_id=HOST, target=t, params=p) for a, t, p in steps],
        reason="spike #337 step 4",
    )


def plans(container: str | None) -> dict[str, Proposal]:
    found = {
        f"redeploy {NAME} by name": plan(("coolify.redeploy", NAME, {})),
        f"redeploy {NAME} by uuid": plan(("coolify.redeploy", UUID, {})),
        f"set_env + redeploy {NAME}": plan(
            ("coolify.set_env", NAME, {"key": "SPIKE_KEY", "value": "1"}),
            ("coolify.redeploy", NAME, {}),
        ),
        f"redeploy {TEST_APP} (control)": plan(("coolify.redeploy", TEST_APP, {})),
    }
    if container:
        found[f"docker.restart {container}"] = plan(("docker.restart", container, {}))
    return found


async def run(label: str, entry: str | None) -> dict:
    hosts = load_hosts_file(HOSTS_FILE)
    config = next(h for h in hosts.hosts if h.id == HOST)
    if entry is not None:
        services = dict(config.services)
        services[NAME] = ServiceOverrides(protected_containers=[entry])
        config = config.model_copy(update={"services": services})
    registry = Registry([config])
    start = time.perf_counter()
    await registry.start()
    await registry.refresh(HOST)
    ms = round((time.perf_counter() - start) * 1000)
    try:
        snapshot = registry.snapshot(HOST) or []
        members = [c for c in snapshot if c.labels.get("coolify.applicationUuid") == UUID]
        container = members[0].name if members else None
        results = {}
        for name, proposal in plans(container).items():
            violations = check_plan(proposal, registry, ENABLED, HOST, incident_containers=[])
            results[name] = [f"{v.rule}: {v.text}" for v in violations]
        return {
            "protection": label,
            "connect_and_read_ms": ms,
            "containers_on_host": len(snapshot),
            "containers_of_app": [c.name for c in members],
            "plans": results,
        }
    finally:
        await registry.close()


async def main() -> list[dict]:
    return [
        await run("none", None),
        await run(f"by name ({NAME})", NAME),
        await run(f"by uuid ({UUID})", UUID),
    ]


print(json.dumps(asyncio.run(main()), indent=2))
