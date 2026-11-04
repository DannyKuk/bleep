"""The evals' MCP server: a fixture's tools served in this process to Bleep's own MCP plugin."""

from pathlib import Path

import pytest
import yaml

from bleep.hosts.registry import Registry
from bleep.tools.default import default_registry
from bleep.tools.registry import ToolContext
from evals.mcp_server import FIXTURES_DIR, McpFixtureError, load_mcp_fixture, serve
from graph_env import RecordingGuardLog


def test_every_shipped_fixture_loads():
    names = sorted(p.stem for p in FIXTURES_DIR.glob("*.yaml"))
    assert names, f"no fixture in {FIXTURES_DIR}"
    for name in names:
        assert load_mcp_fixture(name).tools


def write(directory: Path, body: dict) -> None:
    (directory / "bad.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ({"tools": []}, "tools"),
        ({"tools": [{"name": "a", "description": "x"}], "act_tools": ["b"]}, "does not list"),
        ({"tools": [{"name": "a b", "description": "x"}]}, "name"),
        ({"tools": [{"name": "a", "description": "x"}], "extra": 1}, "extra"),
    ],
)
def test_a_malformed_fixture_is_refused_with_its_file_named(tmp_path, body, why):
    write(tmp_path, body)
    with pytest.raises(McpFixtureError, match="bad.yaml") as refused:
        load_mcp_fixture("bad", tmp_path)
    assert why in str(refused.value)


def test_a_missing_fixture_is_refused(tmp_path):
    with pytest.raises(McpFixtureError, match="nope.yaml"):
        load_mcp_fixture("nope", tmp_path)


async def test_the_plugin_reads_the_fixtures_tools_and_their_schemas_from_the_server():
    log = RecordingGuardLog()
    async with serve(load_mcp_fixture("caddy")) as served:
        plugins = served.plugins(log)
        handler = plugins.handler("mcp.call")
        schemas = await handler.act_tool_schemas("inc_1")
        health = await plugins.view("mcp", version="v")

    assert plugins.actions() == {"mcp.call"}
    assert handler.act_tool_names() == ["caddy_reload", "caddy_purge_cache"]
    assert load_mcp_fixture("caddy").read_tools == ["caddy_config"]
    assert schemas.startswith('<untrusted source="mcp"')
    assert "caddy_reload (act" in schemas and '"config"' in schemas
    assert "may run 1 as read and 2 as act" in health.health.detail
    assert served.calls == []  # listed, never called


async def test_caddy_config_is_a_read_tool_that_shows_the_loaded_upstream_beside_the_file_s():
    async with serve(load_mcp_fixture("caddy")) as served:
        plugins = served.plugins(RecordingGuardLog())
        ctx = ToolContext(incident_id="inc_1", host_id="demo", registry=Registry([]))
        tools = {t.name: t for t in default_registry(plugins=plugins).read_tools(ctx)}
        listing = await tools["mcp_tools"].ainvoke({})
        answer = await tools["mcp_call"].ainvoke(
            {"tool": "caddy_config", "arguments": {"container": "shop-proxy"}}
        )
        refused = await tools["mcp_call"].ainvoke(
            {"tool": "caddy_reload", "arguments": {"container": "shop-proxy"}}
        )

    assert "caddy_config (read, mcp_call runs it)" in listing
    assert "shop-web:8001" in answer and "shop-web:8000" in answer
    assert "loaded config" in answer and "on disk" in answer
    assert "differ" not in answer and "difference" not in answer  # both configs, no conclusion
    assert served.calls == [("caddy_config", {"container": "shop-proxy"})]  # the reload never ran
    assert "caddy_reload" in refused
