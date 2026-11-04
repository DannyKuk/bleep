import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from bleep.plugins.base import PluginContext
from bleep.plugins.coolify.client import CoolifyClient, CoolifyError, CoolifyUnavailable
from bleep.plugins.coolify.plugin import (
    CoolifyPlugin,
    CoolifySettings,
    CoolifyUrlRefused,
    check_url,
)
from bleep.plugins.registry import PluginError, PluginRegistry
from bleep.tools.registry import ToolContext
from fakes import FakeEngine
from graph_env import RecordingGuardLog, box, graph_env, shop_host

FIXTURES = Path(__file__).parent.parent / "fixtures" / "coolify"
BASE = "https://coolify.example.com"
TOKEN = "cool-root-token-7d1e9f"
SECRET = "test-secret"
SHOP = "shop0uuid0000000000000a1"
BLOG = "blog0uuid0000000000000b2"
WRAPPED = re.compile(r'^<untrusted source="coolify" id="(\w+)">\n.*\n</untrusted id="\1">$', re.S)


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def app_box(name: str, uuid: str = SHOP, resource: str = "shop", **fields) -> object:
    labels = {
        "coolify.type": "application",
        "coolify.applicationUuid": uuid,
        "coolify.resourceName": resource,
        "coolify.pullRequestId": "0",
    }
    return box(name, labels=labels | fields.pop("labels", {}), **fields)


class Coolify:
    """A recorded Coolify: answers by method and path, and records every request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: dict[tuple[str, str], httpx.Response] = {
            ("GET", "/api/v1/version"): httpx.Response(200, text="4.6.0"),
            ("GET", "/api/v1/applications"): httpx.Response(200, json=fixture("applications.json")),
            ("GET", f"/api/v1/deployments/applications/{SHOP}"): httpx.Response(
                200, json=fixture("deployments.json")
            ),
            ("GET", f"/api/v1/applications/{SHOP}/envs"): httpx.Response(
                200, json=fixture("envs.json")
            ),
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        found = self.routes.get((request.method, request.url.path))
        return found if found is not None else httpx.Response(404, json={"message": "Not found"})


def context(guard_log=None) -> PluginContext:
    return PluginContext(
        settings=CoolifySettings(),
        secrets={"COOLIFY_URL": SecretStr(BASE), "COOLIFY_TOKEN": SecretStr(TOKEN)},
        grant_secret=SecretStr(SECRET),
        guard_log=guard_log,
    )


def client(coolify, **limits) -> CoolifyClient:
    return CoolifyClient(BASE, SecretStr(TOKEN), transport=httpx.MockTransport(coolify), **limits)


def engine(*extra) -> FakeEngine:
    return FakeEngine(
        "a",
        [
            app_box(f"{SHOP}-074501234567", state="exited", exit_code=1),
            app_box("worker-" + SHOP),
            box("db-1", "db"),
            box("bleep-backend-1", "bleep", labels={"bleep.self": "true"}),
            *extra,
        ],
    )


class Env:
    def __init__(self, built, coolify: Coolify, guard_log) -> None:
        self.registry = built.registry
        self.coolify = coolify
        self.guard_log = guard_log
        plugin = CoolifyPlugin(transport=httpx.MockTransport(coolify))
        self.tools = {spec.name: spec for spec in plugin.tools(context(guard_log))}

    def tool_ctx(self, host_id: str = "a") -> ToolContext:
        return ToolContext("inc_1", host_id, self.registry, self.guard_log)

    async def read(self, name: str, **args) -> str:
        return await self.tools[name].build(self.tool_ctx()).ainvoke(args)


@pytest.fixture
async def env(tmp_path) -> Env:
    return await make_env(tmp_path)


async def make_env(tmp_path, eng: FakeEngine | None = None) -> Env:
    built = await graph_env(tmp_path, engine=eng or engine(), host=shop_host())
    return Env(built, Coolify(), RecordingGuardLog())


# --- the client ---


async def test_the_token_goes_only_into_the_authorization_header():
    coolify = Coolify()

    await client(coolify).applications()

    (request,) = coolify.requests
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in str(request.url)


async def test_deployments_read_the_shape_v4_6_answers_with():
    found = await client(Coolify()).deployments(SHOP)

    assert [d.status for d in found] == ["finished", "failed"]
    assert not hasattr(found[0], "logs")


@pytest.mark.parametrize(
    ("answer", "kind", "text"),
    [
        (httpx.Response(401, json=fixture("unauthenticated.json")), CoolifyError, "token (401)"),
        (httpx.Response(404, json={"message": "x"}), CoolifyError, "no such application"),
        (httpx.Response(503, text="down"), CoolifyUnavailable, "answered 503"),
        (httpx.Response(429), CoolifyUnavailable, "rate limit"),
        (httpx.Response(302, headers={"location": "http://elsewhere.test"}), CoolifyError, "302"),
        (httpx.Response(200, text="<html>login</html>"), CoolifyError, "not JSON"),
        (httpx.Response(200, json={"unexpected": True}), CoolifyError, "unexpected shape"),
    ],
)
async def test_every_failure_is_a_coolify_error_in_bleeps_words(answer, kind, text):
    coolify = Coolify()
    coolify.routes[("GET", "/api/v1/applications")] = answer

    with pytest.raises(kind) as raised:
        await client(coolify).applications()

    assert text in str(raised.value)
    assert TOKEN not in str(raised.value) and BASE not in str(raised.value)
    assert len(coolify.requests) == 1  # a redirect is not followed with the token


async def test_a_request_that_outlasts_its_deadline_is_unavailable():
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json=[])

    with pytest.raises(CoolifyUnavailable, match="did not answer within 0.05 s"):
        await client(slow, deadline_s=0.05).applications()


async def test_an_answer_over_the_size_cap_is_refused_unread():
    def huge(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"[" + b'{"uuid": "x"},' * 200_000 + b"{}]")

    with pytest.raises(CoolifyError, match="over 2 MB"):
        await client(huge).applications()


async def test_one_odd_record_does_not_lose_the_others():
    apps = fixture("applications.json")
    apps[0] |= {"name": None, "status": None}
    apps.append({"name": "no uuid at all"})
    coolify = Coolify()
    coolify.routes[("GET", "/api/v1/applications")] = httpx.Response(200, json=apps)
    deployments = fixture("deployments.json")
    deployments["deployments"][0] |= {"status": None, "is_webhook": None, "pull_request_id": None}
    coolify.routes[("GET", f"/api/v1/deployments/applications/{SHOP}")] = httpx.Response(
        200, json=deployments
    )

    assert [a.uuid for a in await client(coolify).applications()] == [SHOP, BLOG]
    assert [d.status for d in await client(coolify).deployments(SHOP)] == [None, "failed"]


async def test_an_unreachable_coolify_is_unavailable_without_httpxs_message():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot connect to {BASE} with {TOKEN}")

    broken = CoolifyClient(BASE, SecretStr(TOKEN), transport=httpx.MockTransport(refuse))
    with pytest.raises(CoolifyUnavailable) as raised:
        await broken.applications()

    assert str(raised.value) == "Coolify could not be reached (ConnectError)"


# --- the read tools ---


async def test_applications_are_wrapped_untrusted_and_carry_no_secret_field(env):
    out = await env.read("coolify_applications")

    assert WRAPPED.match(out)
    assert f"shop uuid={SHOP} (on this host) status=exited:unhealthy" in out
    assert "domains=https://shop.example.com repo=example/shop@main commit=4b1d3c0" in out
    assert f"blog uuid={BLOG} status=running:healthy" in out
    assert "whsec" not in out and "basic-fake-password" not in out
    assert out.index("shop uuid") < out.index("blog uuid")


async def test_a_token_in_a_repo_url_or_domain_is_never_shown(env):
    apps = fixture("applications.json")
    apps[1]["git_repository"] = "https://oauth2:glpat-fakefakefake0000@gitlab.com/example/blog.git"
    apps[1]["fqdn"] = "https://blog.example.com,https://admin:hunter2-fake@blog-admin.example.com"
    env.coolify.routes[("GET", "/api/v1/applications")] = httpx.Response(200, json=apps)

    out = await env.read("coolify_applications")

    assert "glpat" not in out and "hunter2" not in out and "oauth2" not in out
    assert "repo=https://gitlab.com/example/blog.git@main" in out
    assert "domains=https://blog.example.com,https://blog-admin.example.com" in out


async def test_deployments_by_name_show_status_commit_and_trigger_but_never_logs(env):
    out = await env.read("coolify_deployments", app="shop")

    assert WRAPPED.match(out)
    assert "finished commit=4b1d3c0 (webhook)" in out
    assert "Read PORT from the environment" in out and "The old default" not in out
    assert "failed commit=0ddba11 (api) finished=not yet" in out
    assert "fake-db-password" not in out


async def test_env_keys_never_show_a_value(env):
    out = await env.read("coolify_env_keys", app=SHOP)

    assert "- DATABASE_URL (runtime)" in out
    assert "- STRIPE_KEY (build, runtime)" in out
    assert "- STRIPE_KEY (build, runtime, preview)" in out
    assert "fake-db-password" not in out and "sk_live" not in out and "sk_test" not in out


@pytest.mark.parametrize(
    ("app", "said"),
    [("nope", "Coolify has no application nope"), ("", "Tool input validation error")],
)
async def test_an_unknown_or_empty_application_is_a_readable_result(env, app, said):
    assert said in await env.read("coolify_deployments", app=app)


async def test_two_applications_by_one_name_ask_for_the_uuid(env):
    apps = fixture("applications.json")
    apps[1]["name"] = "shop"
    env.coolify.routes[("GET", "/api/v1/applications")] = httpx.Response(200, json=apps)

    assert "2 Coolify applications are named shop" in await env.read("coolify_env_keys", app="shop")


async def test_a_coolify_failure_is_a_result_for_the_model_not_a_crash(env):
    env.coolify.routes[("GET", "/api/v1/applications")] = httpx.Response(401)

    out = await env.read("coolify_applications")

    assert out == "Coolify could not be read: Coolify refused the token (401); check COOLIFY_TOKEN."


async def test_the_tools_are_offered_only_on_a_host_running_coolify_applications(tmp_path):
    with_apps = await make_env(tmp_path / "1")
    without = await make_env(tmp_path / "2", FakeEngine("a", [box("db-1", "db")]))

    for spec in with_apps.tools.values():
        assert spec.offered(with_apps.tool_ctx())
        assert not spec.offered(without.tool_ctx())


# --- the plugin ---

ENV = {"COOLIFY_URL": BASE, "COOLIFY_TOKEN": TOKEN}


def registry(coolify: Coolify, env: dict[str, str] | None = None) -> PluginRegistry:
    plugin = CoolifyPlugin(transport=httpx.MockTransport(coolify))
    return PluginRegistry([plugin], SecretStr(SECRET), env=ENV if env is None else env)


async def test_switched_on_it_adds_its_read_tools_and_no_action():
    plugins = registry(Coolify())
    plugins.set("coolify", {}, True)

    assert {s.name for s in plugins.tool_specs()} == {
        "coolify_applications",
        "coolify_deployments",
        "coolify_env_keys",
    }
    assert all(s.kind == "read" for s in plugins.tool_specs())
    assert plugins.actions() == set()


@pytest.mark.parametrize("missing", ["COOLIFY_URL", "COOLIFY_TOKEN"])
async def test_it_cannot_be_switched_on_without_the_url_and_the_token(missing):
    plugins = registry(Coolify(), env={k: v for k, v in ENV.items() if k != missing})

    with pytest.raises(PluginError, match=missing):
        plugins.set("coolify", {}, True)


async def test_the_address_is_no_setting_a_person_can_change():
    plugins = registry(Coolify())

    with pytest.raises(PluginError, match="base_url"):
        plugins.set("coolify", {"base_url": "https://attacker.example"}, True)

    assert not plugins.is_enabled("coolify")


@pytest.mark.parametrize(
    "url",
    [
        "https://coolify.example.com",
        "https://1.1.1.1:8000/",
        "http://10.0.0.5:8000",
        "http://192.168.1.20:8000",
        "http://127.0.0.1:8000",
        "http://100.101.102.103:8000",  # Tailscale
        "http://[::1]:8000",
        "http://coolify:8000",  # a Docker service name
    ],
)
def test_https_or_a_nearby_address_is_accepted(url):
    assert check_url(url) == url.rstrip("/")


@pytest.mark.parametrize(
    "url",
    [
        "http://1.1.1.1:8000",  # a public address in clear
        "http://coolify.example.com",  # a public name in clear; no DNS lookup is made
        "ftp://10.0.0.5",
        "https://admin:pw@coolify.example.com",
        "coolify.example.com",
    ],
)
def test_a_url_the_token_must_not_travel_to_is_refused(url):
    with pytest.raises(CoolifyUrlRefused) as refused:
        check_url(url)

    assert url not in str(refused.value)


async def test_a_public_http_url_keeps_the_plugin_off_and_is_never_called():
    coolify = Coolify()
    plugins = registry(coolify, env=ENV | {"COOLIFY_URL": "http://1.1.1.1:8000"})

    with pytest.raises(PluginError, match="CoolifyUrlRefused"):
        plugins.set("coolify", {}, True)

    assert coolify.requests == []


async def test_health_names_the_version_and_never_the_token():
    plugins = registry(Coolify())
    plugins.set("coolify", {}, True)

    health = (await plugins.view("coolify")).health

    assert health is not None and health.ok
    assert health.detail == "Coolify 4.6.0 answers with the token"


async def test_a_refused_token_is_unhealthy_in_bleeps_words():
    coolify = Coolify()
    coolify.routes[("GET", "/api/v1/version")] = httpx.Response(401)
    plugins = registry(coolify)
    plugins.set("coolify", {}, True)

    health = (await plugins.view("coolify")).health

    assert health is not None and not health.ok
    assert health.detail == "Coolify refused the token (401); check COOLIFY_TOKEN"
    assert TOKEN not in health.detail


async def test_a_version_answer_that_is_not_a_version_is_not_repeated():
    coolify = Coolify()
    coolify.routes[("GET", "/api/v1/version")] = httpx.Response(200, text="<b>ignore all</b>")
    plugins = registry(coolify)
    plugins.set("coolify", {}, True)

    health = (await plugins.view("coolify")).health

    assert health is not None and health.detail == "Coolify answers with the token"
