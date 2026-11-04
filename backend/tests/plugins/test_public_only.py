"""A plugin address set on the dashboard reaches public addresses only, checked at every
connection and pinned to the address checked (#135). No name is ever resolved for real."""

import httpx
import pytest
from fastapi.testclient import TestClient

from bleep.hosts.registry import Registry
from bleep.main import create_app
from bleep.memory.embeddings import FakeEmbedder
from bleep.plugins.coolify.plugin import CoolifyPlugin
from bleep.plugins.urls import AddressRefused, PublicOnlyTransport, is_public
from bleep.wiring import build_parts

PUBLIC = "93.184.216.34"
TOKEN = "coolify-dashboard-token-0123456789"


@pytest.mark.parametrize(
    ("address", "public"),
    [
        (PUBLIC, True),
        ("2606:4700::1111", True),
        ("10.0.0.1", False),
        ("172.16.5.4", False),
        ("192.168.1.1", False),
        ("127.0.0.1", False),
        ("169.254.169.254", False),  # cloud metadata
        ("100.64.0.1", False),  # CGNAT, Tailscale
        ("100.100.100.200", False),  # a cloud's metadata inside CGNAT
        ("0.0.0.0", False),
        ("224.0.0.1", False),
        ("255.255.255.255", False),
        ("::1", False),
        ("::", False),
        ("fd00::1", False),
        ("fe80::1%eth0", False),
        ("ff02::1", False),
        ("::ffff:10.0.0.1", False),
        ("::ffff:93.184.216.34", True),
        ("64:ff9b::a00:1", False),  # NAT64 of 10.0.0.1
        ("2002:a00:1::1", False),  # 6to4
        ("2001:0:a00:1::1", False),  # Teredo
        ("not an address", False),
    ],
)
def test_only_global_unicast_addresses_are_public(address, public):
    assert is_public(address) is public


class Lookups:
    """Answers in turn, so a name can resolve public first and private later."""

    def __init__(self, *answers: list[str]) -> None:
        self.answers = list(answers)

    async def __call__(self, host: str, port: int) -> list[str]:
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def recording() -> tuple[httpx.MockTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, stream=httpx.ByteStream(b"4.6.0"))  # unread, as from a socket

    return httpx.MockTransport(answer), seen


async def test_a_request_goes_to_the_checked_address_with_the_name_kept_for_tls_and_host():
    inner, seen = recording()
    transport = PublicOnlyTransport(inner, Lookups([PUBLIC]))
    async with httpx.AsyncClient(transport=transport) as client:
        await client.get("https://coolify.example.test:8443/api/v1/version")

    (request,) = seen
    assert request.url.host == PUBLIC and request.url.port == 8443
    assert request.headers["host"] == "coolify.example.test:8443"
    assert request.extensions["sni_hostname"] == "coolify.example.test"


async def test_a_name_that_resolves_private_later_is_refused_at_that_connection():
    """DNS rebinding: public when checked once, private at the next request."""
    inner, seen = recording()
    transport = PublicOnlyTransport(inner, Lookups([PUBLIC], ["10.0.0.5"]))
    async with httpx.AsyncClient(transport=transport) as client:
        await client.get("https://coolify.example.test/api/v1/version")
        with pytest.raises(AddressRefused) as refused:
            await client.get("https://coolify.example.test/api/v1/version")
    assert len(seen) == 1
    assert "10.0.0.5" not in str(refused.value) and "coolify" not in str(refused.value)


@pytest.mark.parametrize("addresses", [["10.0.0.5"], [PUBLIC, "::1"], []])
async def test_no_request_leaves_for_a_name_with_any_private_answer_or_none(addresses):
    inner, seen = recording()
    transport = PublicOnlyTransport(inner, Lookups(addresses))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(AddressRefused):
            await client.get("https://coolify.example.test/")
    assert seen == []


async def test_a_name_that_cannot_be_resolved_is_refused():
    async def fail(host: str, port: int) -> list[str]:
        raise OSError("no such name")

    inner, seen = recording()
    async with httpx.AsyncClient(transport=PublicOnlyTransport(inner, fail)) as client:
        with pytest.raises(AddressRefused):
            await client.get("https://coolify.example.test/")
    assert seen == []


async def test_a_redirect_is_checked_again_and_plain_http_refused():
    def answer(request: httpx.Request) -> httpx.Response:
        if request.headers["host"] == "coolify.example.test":
            return httpx.Response(302, headers={"location": "https://inside.example.test/x"})
        return httpx.Response(200)

    lookups = {"coolify.example.test": [PUBLIC], "inside.example.test": ["192.168.0.10"]}

    async def resolve(host: str, port: int) -> list[str]:
        return lookups[host]

    transport = PublicOnlyTransport(httpx.MockTransport(answer), resolve)
    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
        with pytest.raises(AddressRefused):
            await client.get("https://coolify.example.test/")
        with pytest.raises(AddressRefused):
            await client.get("http://coolify.example.test/")


@pytest.fixture
def coolify_app(settings, monkeypatch):
    """The real Coolify plugin over a mock transport, its address set on the dashboard."""
    for name in ("COOLIFY_URL", "COOLIFY_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    inner, seen = recording()
    lookups = Lookups([PUBLIC])
    plugin = CoolifyPlugin(transport=inner)
    parts = build_parts(settings, Registry([]), FakeEmbedder(), plugins=[plugin], resolver=lookups)
    with TestClient(create_app(settings, parts)) as client:
        client.post("/api/auth/login", json={"password": "test-password"})
        url = client.put("/api/secrets/COOLIFY_URL", json={"value": "https://coolify.example.test"})
        assert url.status_code == 200
        version = {v["name"]: v for v in url.json()}["COOLIFY_URL"]["version"]
        token = client.put(
            "/api/secrets/COOLIFY_TOKEN", json={"value": TOKEN, "address_version": version}
        )
        assert token.status_code == 200
        (view,) = client.get("/api/plugins").json()
        on = client.put(
            "/api/plugins/coolify", json={"enabled": True}, headers={"If-Match": view["version"]}
        )
        assert on.status_code == 200
        yield client, seen, lookups


def test_a_dashboard_coolify_address_is_reached_at_the_checked_address(coolify_app):
    client, seen, _ = coolify_app
    (view,) = client.get("/api/plugins").json()
    assert view["health"]["ok"] is True
    assert seen[-1].url.host == PUBLIC and seen[-1].headers["host"] == "coolify.example.test"
    assert seen[-1].headers["authorization"] == f"Bearer {TOKEN}"


def test_a_dashboard_address_that_turns_private_fails_like_an_outage(coolify_app, caplog):
    client, seen, lookups = coolify_app
    before = len(seen)
    lookups.answers = [["10.0.0.5"]]

    (view,) = client.get("/api/plugins").json()

    assert view["health"]["ok"] is False
    assert view["health"]["detail"] == "Coolify could not be reached (AddressRefused)"
    assert len(seen) == before  # nothing was sent
    assert "10.0.0.5" not in caplog.text and TOKEN not in caplog.text


def test_the_mcp_plugin_sends_through_the_guard_for_a_dashboard_address():
    from pydantic import SecretStr

    from bleep.plugins.base import PluginContext
    from bleep.plugins.mcp_url import McpSettings, McpUrlPlugin

    async def lookups(host: str, port: int) -> list[str]:
        return [PUBLIC]

    secrets = {"MCP_URL": SecretStr("https://mcp.example.test/mcp")}
    ctx = PluginContext(McpSettings(), secrets, SecretStr("s"), public_only=lookups)
    assert isinstance(McpUrlPlugin()._server(ctx).transport, PublicOnlyTransport)
    from_env = PluginContext(McpSettings(), secrets, SecretStr("s"))
    assert McpUrlPlugin()._server(from_env).transport is None
