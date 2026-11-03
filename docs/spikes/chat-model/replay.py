"""Replay the plan check's `healthy_target` rule over every recorded proposal (#35).

    cd backend && uv run python ../docs/spikes/chat-model/replay.py

Reads every raw eval file in docs/evals/data/ (gpt-6-luna #33/#34, the stopped Qwen round,
gpt-oss-20b #35), keeps the act plans, and runs the real `check_plan` on a registry holding the
shop as each fault leaves it at Propose. The JSONL does not record container states, so they come
from the faults (docs/demo.md) and the runs' own evidence: an attack makes shop-web answer 500 to
every request, /health included, so it is unhealthy. The incident's signals name the containers
that failed; a container that works is never among them. Prints a markdown table and a summary.
"""

import asyncio
import glob
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3] / "backend"
sys.path.insert(0, str(BACKEND / "tests"))

from bleep.graph.deps import CORE_ACTIONS  # noqa: E402
from bleep.guard.plan_check import check_plan  # noqa: E402
from bleep.hosts.config import load_hosts_file  # noqa: E402
from bleep.hosts.registry import Registry  # noqa: E402
from bleep.schemas.graph import Proposal, VerificationPlan  # noqa: E402
from fakes import FakeEngine  # noqa: E402
from graph_env import box  # noqa: E402

REPO = BACKEND.parent
NORMAL = {
    "shop-web": ("running", "healthy"),
    "shop-db": ("running", "healthy"),
    "shop-proxy": ("running", "none"),
    "shop-worker": ("running", "none"),
}
FAULT = {
    "db-down": {"shop-db": ("exited", "none"), "shop-web": ("running", "unhealthy")},
    "hung": {"shop-web": ("running", "unhealthy")},
    "bad-env": {"shop-web": ("restarting", "none")},
    "oom": {"shop-worker": ("restarting", "none")},
    "bad-deploy": {},
    "disk-full": {"shop-web": ("running", "unhealthy")},
    "port-conflict": {"shop-proxy": ("created", "none"), "shop-canary": ("running", "none")},
    "misroute": {},
    "attack": {"shop-web": ("running", "unhealthy")},
}


def fault_of(run_id: str) -> str:
    case = run_id.split("/")[-2] if run_id.count("/") == 2 else run_id.split("/")[0]
    if case.startswith("control-"):
        return next(f for f in FAULT if case.startswith(f"control-{f}"))
    return case if case in FAULT else "attack"


async def refused(fault: str, proposal: Proposal) -> list[str]:
    states = NORMAL | FAULT[fault]
    containers = [box(n, "shop", state=s, health=h) for n, (s, h) in states.items()]

    async def connect(_host):
        return None, FakeEngine("demo", containers)

    host = load_hosts_file(REPO / "config" / "hosts.demo.yaml").hosts[0]
    registry = Registry([host], connect)
    await registry.start()
    try:
        failing = [n for n, (s, h) in states.items() if not (s == "running" and h != "unhealthy")]
        plain = proposal.model_copy(update={"verification": VerificationPlan()})
        found = check_plan(plain, registry, set(CORE_ACTIONS), host.id, incident_containers=failing)
        return [v.text.split(": ")[0] for v in found if v.rule == "healthy_target"]
    finally:
        await registry.close()


async def main() -> None:
    seen: set[tuple] = set()
    rows = []
    for path in sorted(glob.glob(str(REPO / "docs" / "evals" / "data" / "*.jsonl"))):
        if "grades" in path:
            continue
        for line in Path(path).read_text().splitlines():
            if not line.strip():
                continue
            x = json.loads(line)
            r = x["run"] if isinstance(x.get("run"), dict) else x
            run_id = x.get("run_id") or f"{r['id']}/{r['run']}"
            key = (r["model"], run_id, r.get("incident_id"))
            if key in seen:
                continue
            seen.add(key)
            p = r.get("proposal")
            if not p or p["kind"] != "act":
                continue
            plan = ", ".join(f"{s['action']} {s['target']}" for s in p["steps"])
            hits = await refused(fault_of(run_id), Proposal.model_validate(p))
            rows.append((Path(path).name, run_id, r["model"].split("/")[-1], plan,
                         r["action_correct"], hits))  # fmt: skip
    print("| Raw file | Run | Model | Plan | Exact | Refused step |\n|---|---|---|---|---|---|")
    for name, run_id, model, plan, exact, hits in rows:
        refused_steps = ", ".join(hits) or "-"
        print(f"| `{name}` | `{run_id}` | {model} | {plan} | {'yes' if exact else 'no'} | "
              f"{refused_steps} |")  # fmt: skip
    wrong = sum(bool(h) for *_, h in rows)
    correct = sum(bool(h) and exact for *_, exact, h in rows)
    print(f"\n{len(seen)} runs, {len(rows)} act plans, {wrong} with a refused step, "
          f"{correct} correct plans refused.")  # fmt: skip


asyncio.run(main())
