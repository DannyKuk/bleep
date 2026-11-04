"""An MCP server for the evals: a fixture's tools served in this process to Bleep's own MCP
plugin, so its tools and `mcp.call` run unchanged and nothing leaves the machine.

A fixture is a YAML file in `evals/fixtures/mcp/`: the tools the server lists (name, description,
input schema) and which of them the plugin's settings allow as read or act tools. The server is
the `mcp` package's own low-level server over Streamable HTTP, reached through an
`httpx.ASGITransport` as in the plugin's tests, so no socket is opened. A call answers the tool's
fixed `result` (or "<name>: done") and is recorded; evals stop after Propose, so an act tool is
never called.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

import httpx
import yaml
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator

from bleep.guard.log import GuardLogger
from bleep.plugins.mcp_url import McpUrlPlugin
from bleep.plugins.registry import PluginRegistry

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "mcp"
# Only the plugin's URL rule reads it: the transport answers every request in this process.
URL = "https://mcp.eval.invalid/mcp"
GRANT_SECRET = SecretStr("evals-never-execute")


class McpFixtureError(ValueError):
    """An MCP fixture is missing or malformed."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FixtureTool(_Strict):
    name: str = Field(pattern=r"^[A-Za-z0-9_.\-/]{1,128}$")
    description: str
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    result: str | None = None  # what a call answers; "<name>: done" when absent


class McpFixture(_Strict):
    tools: list[FixtureTool] = Field(min_length=1)
    read_tools: list[str] = Field(default_factory=list)
    act_tools: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _allowed_tools_are_listed(self) -> Self:
        listed = {t.name for t in self.tools}
        unknown = sorted({*self.read_tools, *self.act_tools} - listed)
        if unknown:
            raise ValueError(f"the settings allow tools the server does not list: {unknown}")
        return self


def load_mcp_fixture(name: str, directory: Path = FIXTURES_DIR) -> McpFixture:
    path = directory / f"{name}.yaml"
    try:
        return McpFixture.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise McpFixtureError(f"MCP fixture {path}: {error}") from error


@dataclass
class ServedMcp:
    """A fixture being served: the transport that reaches it, and every call it answered."""

    fixture: McpFixture
    transport: httpx.AsyncBaseTransport
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def plugins(self, guard_log: GuardLogger | None = None) -> PluginRegistry:
        """Bleep's MCP plugin on this server, switched on with the fixture's settings."""
        registry = PluginRegistry(
            [McpUrlPlugin(self.transport)], GRANT_SECRET, guard_log=guard_log, env={"MCP_URL": URL}
        )
        settings = {"read_tools": self.fixture.read_tools, "act_tools": self.fixture.act_tools}
        registry.set("mcp", settings, True)
        return registry


@asynccontextmanager
async def serve(fixture: McpFixture) -> AsyncIterator[ServedMcp]:
    """Serve `fixture` until the block ends."""
    server: Server = Server("bleep-evals")
    listed = [
        types.Tool(name=t.name, description=t.description, inputSchema=t.input_schema)
        for t in fixture.tools
    ]
    results = {t.name: t.result for t in fixture.tools}
    manager = StreamableHTTPSessionManager(
        app=server,
        json_response=True,
        stateless=True,
        security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    async def app(scope, receive, send) -> None:
        await manager.handle_request(scope, receive, send)

    served = ServedMcp(fixture, httpx.ASGITransport(app=app))

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return listed

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        served.calls.append((name, arguments))
        return [types.TextContent(type="text", text=results.get(name) or f"{name}: done")]

    async with manager.run():
        yield served
