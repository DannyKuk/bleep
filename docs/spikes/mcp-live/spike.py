"""Spike #371: Bleep's MCP plugin against Coolify's own MCP server.

Runs inside the backend container, which holds `MCP_URL` and `MCP_TOKEN` in the dashboard's
secret file (or the env), and calls no model:

    docker exec -i -e SPIKE_READ='tool_a,tool_b' -e SPIKE_CALLS='[["tool_a", {}]]' \
        bleep-backend-1 /app/.venv/bin/python - < docs/spikes/mcp-live/spike.py

It builds the server as the plugin does (`check_url`, then `_Server`), with `SPIKE_READ` as the
read tools and no act tools, and prints one JSON document: the health check, every tool the
server lists (its annotations, description and schema sizes), the `mcp_tools` listing as the
model would get it, and each call in `SPIKE_CALLS` through `mcp_call`, with latency, output
size, whether it fits the guard's cap and how much was redacted. Raw answers are kept in the
container under /tmp/spike371 only. The token is never printed: the output only says how often
it appeared.
"""

import asyncio
import json
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace

from bleep import prompts
from bleep.guard.sanitize import MAX_CHARS
from bleep.plugins import urls
from bleep.plugins.mcp_url import (
    TOKEN_ENV,
    URL_ENV,
    McpSettings,
    McpUrlRefused,
    _read_tools,
    _Server,
)
from bleep.tools.registry import ToolContext
from pydantic import SecretStr

RAW = Path("/tmp/spike371")
SECRETS = Path("/data/secrets.json")
WRAPPED = re.compile(
    r'^<untrusted source="(\w+)" id="([0-9a-f]+)">\n.*\n</untrusted id="\2">$',
    re.DOTALL,
)
# Strings in a raw answer that look like credentials and must not reach the model unredacted.
SECRETISH = re.compile(
    r"(?i)(password|secret|token|api[_-]?key|private[_-]?key)[\"']?\s*[:=]\s*[\"']?(?!<redacted)([^\s\"',}]{6,})"
)


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def record(self, incident_id: str, kind: str, detail: str) -> None:
        self.events.append((kind, detail))


class NoContainers:
    """A registry with one host and no containers, so no argument names one of Bleep's own."""

    def hosts(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(config=SimpleNamespace(id="coolify"))]

    def snapshot(self, host_id: str) -> list:
        return []


def secret(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    return json.loads(SECRETS.read_text())["values"][name]


def shape(text: str) -> dict:
    match = WRAPPED.match(text)
    return {
        "chars": len(text),
        "within_cap": len(text) <= MAX_CHARS,
        "wrapped": bool(match),
        "redacted": text.count("<redacted"),
    }


async def timed(coro) -> tuple[object, float]:
    start = time.perf_counter()
    result = await coro
    return result, round((time.perf_counter() - start) * 1000, 1)


async def main() -> dict:
    url = urls.check_url(secret(URL_ENV), URL_ENV, McpUrlRefused)
    token = SecretStr(secret(TOKEN_ENV))
    read = [t for t in os.environ.get("SPIKE_READ", "").split(",") if t]
    calls = json.loads(os.environ.get("SPIKE_CALLS", "[]"))
    # A URL set on the dashboard goes through the public-only transport, as the registry does.
    from_dashboard = not os.environ.get(URL_ENV)
    transport = urls.guarded(None, urls.resolve if from_dashboard else None)
    server = _Server(url, McpSettings(read_tools=read), token, transport)
    report: dict = {"read_tools": read, "url_from_dashboard": from_dashboard}
    RAW.mkdir(exist_ok=True)

    tools, ms = await timed(server.tools())
    report["list_ms"] = ms
    report["tools"] = [
        {
            "name": t.name,
            "kind": server.kind(t),
            "annotations": {
                k: v for k, v in (t.metadata or {}).items() if k.endswith("Hint")
            },
            "description_chars": len(t.description or ""),
            "schema_chars": len(json.dumps(t.args_schema)),
            "required": (t.args_schema or {}).get("required"),
        }
        for t in tools
    ]
    (RAW / "tools.json").write_text(
        json.dumps(
            [
                {
                    "name": t.name,
                    "description": t.description,
                    "schema": t.args_schema,
                    "metadata": t.metadata,
                }
                for t in tools
            ],
            indent=1,
        )
    )

    recorder = Recorder()
    ctx = ToolContext(
        incident_id="spike-371",
        host_id="coolify",
        registry=NoContainers(),  # type: ignore[arg-type]
        guard_log=recorder,  # type: ignore[arg-type]
        texts=prompts.load,
    )
    specs = {spec.name: spec.build(ctx) for spec in _read_tools(server)}
    listing, ms = await timed(specs["mcp_tools"].ainvoke({}))
    left = re.search(r"\((\d+) more tools? not shown", listing)
    report["mcp_tools"] = {
        "ms": ms,
        "listed_by_server": len(tools),
        "not_shown": int(left.group(1)) if left else 0,
        **shape(listing),
        "text": listing,
    }

    token_value = token.get_secret_value()
    report["calls"] = []
    for i, (name, args) in enumerate(calls):
        raw, raw_ms = await timed(server.call(name, args, "read"))
        (RAW / f"call{i}-{name}.txt").write_text(raw.text)
        text, ms = await timed(
            specs["mcp_call"].ainvoke({"tool": name, "arguments": args})
        )
        report["calls"].append(
            {
                "tool": name,
                "args": args,
                "status": raw.status,
                "raw_chars": len(raw.text),
                "raw_ms": raw_ms,
                "ms": ms,
                **shape(text),
                "secretish_in_raw": len(SECRETISH.findall(raw.text)),
                "secretish_in_output": len(SECRETISH.findall(text)),
                "token_in_raw": raw.text.count(token_value),
                "token_in_output": text.count(token_value),
                "text": text,
            }
        )
    report["guard_events"] = recorder.events
    return report


print(json.dumps(asyncio.run(main()), indent=1))
