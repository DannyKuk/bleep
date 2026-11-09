"""Spike #337: Bleep's Coolify read tools and health check on a real Coolify server.

Runs inside the backend container, which has `COOLIFY_URL` and `COOLIFY_TOKEN`, and calls no
model and nothing that writes:

    docker cp backend/tests/fixtures/coolify bleep-backend-1:/tmp/fixtures-coolify
    docker exec -i bleep-backend-1 /app/.venv/bin/python - < docs/spikes/coolify-live/spike.py

It builds the client as the plugin does (`check_url`, then `CoolifyClient`), runs the health
check's request and the three read tools on the test application (`SPIKE_APP`, default
`bleep-testapp`), and prints one JSON document: each call's latency, output size and shape
checks, whether any value the answers hold but Bleep must drop (webhook secrets, env values)
reached the output, and the field names of each raw answer next to the test fixture's, so the
shapes can be compared. Raw answers are kept in the container under /tmp/spike337 only. The
token is never printed: the output only says how often it appeared.
"""

import asyncio
import json
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
from pydantic import SecretStr

from bleep.guard.sanitize import MAX_CHARS
from bleep.plugins.coolify.client import CoolifyClient
from bleep.plugins.coolify.plugin import TOKEN_ENV, URL_ENV, check_url, read_specs
from bleep.tools.registry import ToolContext

RAW = Path("/tmp/spike337")
APP = os.environ.get("SPIKE_APP", "bleep-testapp")
FIXTURES = Path("/tmp/fixtures-coolify")  # the image has no tests; copied in, see above
WRAPPED = re.compile(
    r'^<untrusted source="(\w+)" id="([0-9a-f]+)">\n.*\n</untrusted id="\2">$', re.S
)
# Fields of a raw application or env answer whose values must never reach a tool's output.
SECRET_FIELDS = (
    "manual_webhook_secret_github",
    "manual_webhook_secret_gitlab",
    "manual_webhook_secret_bitbucket",
    "manual_webhook_secret_gitea",
    "http_basic_auth_password",
    "value",
    "real_value",
)


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def record(self, incident_id: str, kind: str, detail: str) -> None:
        self.events.append((kind, detail))


class OneHost:
    """A registry whose one host runs a container of the test application, so the tools mark it
    "(on this host)"; no container is Bleep's own."""

    def __init__(self, uuid: str) -> None:
        self.container = SimpleNamespace(labels={"coolify.applicationUuid": uuid})

    def hosts(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(config=SimpleNamespace(id="coolify"))]

    def snapshot(self, host_id: str) -> list[SimpleNamespace]:
        return [self.container]


OUTPUTS: list[str] = []


def shape(text: str) -> dict:
    match = WRAPPED.match(text)
    return {
        "chars": len(text),
        "within_cap": len(text) <= MAX_CHARS,
        "wrapped": bool(match),
        "source": match.group(1) if match else None,
        "redacted": text.count("<redacted"),
    }


async def timed(coro) -> tuple[object, float]:
    start = time.perf_counter()
    result = await coro
    return result, round((time.perf_counter() - start) * 1000, 1)


async def raw_get(base: str, token: SecretStr, path: str, name: str) -> dict:
    headers = {"Authorization": f"Bearer {token.get_secret_value()}", "Accept": "application/json"}
    async with httpx.AsyncClient(base_url=base + "/api/v1", headers=headers, timeout=20) as http:
        start = time.perf_counter()
        response = await http.get(path)
        ms = round((time.perf_counter() - start) * 1000, 1)
    RAW.mkdir(exist_ok=True)
    (RAW / f"{name}.json").write_bytes(response.content)
    return {"status": response.status_code, "ms": ms, "data": response.json()}


def fields(data: object) -> list[str]:
    """The field names of an answer's first record (a list, `{deployments: [...]}` or a dict)."""
    if isinstance(data, dict) and isinstance(data.get("deployments"), list):
        data = data["deployments"]
    if isinstance(data, list):
        data = data[0] if data else {}
    return sorted(data) if isinstance(data, dict) else []


def fixture_fields(name: str) -> list[str]:
    path = FIXTURES / f"{name}.json"
    return fields(json.loads(path.read_text())) if path.exists() else []


def compare(name: str, live: object) -> dict:
    live_fields, fixture = set(fields(live)), set(fixture_fields(name))
    return {
        "live_fields": len(live_fields),
        "only_live": sorted(live_fields - fixture),
        "only_fixture": sorted(fixture - live_fields),
    }


def secret_values(records: object) -> list[str]:
    """Every non-trivial value a raw answer holds in a field Bleep must drop."""
    if isinstance(records, dict):
        records = [records]
    found = []
    for record in records if isinstance(records, list) else []:
        for field in SECRET_FIELDS:
            value = record.get(field) if isinstance(record, dict) else None
            if isinstance(value, str) and len(value) >= 4:
                found.append(value)
    return found


async def main() -> dict:
    url = check_url(os.environ[URL_ENV])
    token = SecretStr(os.environ[TOKEN_ENV])
    client = CoolifyClient(url, token)
    report: dict = {}

    version, ms = await timed(client.version())
    report["health"] = {"version": version, "ms": ms}

    raw_apps = await raw_get(url, token, "/applications", "applications")
    apps = raw_apps["data"]
    target = next(a for a in apps if a.get("name") == APP or a.get("uuid") == APP)
    uuid = target["uuid"]
    raw_deps = await raw_get(url, token, f"/deployments/applications/{uuid}?take=5", "deployments")
    raw_envs = await raw_get(url, token, f"/applications/{uuid}/envs", "envs")
    report["raw"] = {
        name: {"status": raw["status"], "ms": raw["ms"], **compare(name, raw["data"])}
        for name, raw in (
            ("applications", raw_apps),
            ("deployments", raw_deps),
            ("envs", raw_envs),
        )
    }
    report["raw"]["applications"]["count"] = len(apps)
    report["raw"]["envs"]["rows"] = [
        {"key": e.get("key"), "is_preview": e.get("is_preview"), "has_value": "value" in e}
        for e in raw_envs["data"]
    ]
    report["raw"]["deployments"]["has_logs"] = any(
        "logs" in d for d in raw_deps["data"].get("deployments", [])
    )

    recorder = Recorder()
    ctx = ToolContext(
        incident_id="spike-337",
        host_id="coolify",
        registry=OneHost(uuid),  # type: ignore[arg-type]
        guard_log=recorder,  # type: ignore[arg-type]
    )
    tools = {spec.name: spec.build(ctx) for spec in read_specs(client)}
    report["offered"] = {spec.name: spec.offered(ctx) for spec in read_specs(client)}
    calls = [
        ("coolify_applications", {}),
        ("coolify_deployments", {"app": APP}),
        ("coolify_env_keys", {"app": APP}),
        ("coolify_env_keys", {"app": uuid}),
        ("coolify_env_keys", {"app": "no-such-app-337"}),
        ("coolify_deployments", {"app": "../applications"}),
    ]
    report["tools"] = []
    for name, args in calls:
        text, ms = await timed(tools[name].ainvoke(args))
        OUTPUTS.append(text)
        report["tools"].append({"tool": name, "args": args, "ms": ms, **shape(text), "text": text})

    leaked = secret_values(apps) + secret_values(raw_envs["data"])
    joined = "\n".join(OUTPUTS)
    report["leaks"] = {
        "secret_values_in_answers": len(leaked),
        "secret_values_in_outputs": sum(value in joined for value in leaked),
        "token_in_outputs": joined.count(token.get_secret_value()),
    }
    report["guard_log"] = recorder.events
    return report


print(json.dumps(asyncio.run(main()), indent=2))
