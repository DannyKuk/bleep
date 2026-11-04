import json
import re
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from bleep.plugins.base import PluginContext
from bleep.plugins.coolify.client import CoolifyClient, CoolifyError, CoolifyUnavailable
from bleep.plugins.coolify.plugin import CoolifyPlugin, CoolifySettings
from bleep.plugins.registry import PluginRegistry
from bleep.tools.registry import ToolContext
from fakes import FakeEngine
from graph_env import RecordingGuardLog, box, graph_env, shop_host

FIXTURES = Path(__file__).parent.parent / "fixtures" / "coolify"
BASE = "http://coolify.test:8000"
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
        settings=CoolifySettings(base_url=BASE),
        secrets={"COOLIFY_TOKEN": SecretStr(TOKEN)},
        grant_secret=SecretStr(SECRET),
        guard_log=guard_log,
    )


def client(coolify: Coolify) -> CoolifyClient:
    return CoolifyClient(BASE, SecretStr(TOKEN), transport=httpx.MockTransport(coolify))


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
    assert f"blog uuid={BLOG} status=running:healthy" in out
    assert "whsec" not in out and "basic-fake-password" not in out
    assert out.index("shop uuid") < out.index("blog uuid")


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
    [("nope", "Coolify has no application nope"), ("", "invalid arguments")],
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


def registry(coolify: Coolify, env: dict[str, str] | None = None) -> PluginRegistry:
    plugin = CoolifyPlugin(transport=httpx.MockTransport(coolify))
    return PluginRegistry(
        [plugin], SecretStr(SECRET), env={"COOLIFY_TOKEN": TOKEN} if env is None else env
    )


async def test_switched_on_it_adds_its_read_tools_and_no_action():
    plugins = registry(Coolify())
    plugins.set("coolify", {"base_url": BASE + "/"}, True)

    assert {s.name for s in plugins.tool_specs()} == {
        "coolify_applications",
        "coolify_deployments",
        "coolify_env_keys",
    }
    assert all(s.kind == "read" for s in plugins.tool_specs())
    assert plugins.actions() == set()
    assert plugins.settings("coolify").model_dump() == {"base_url": BASE}


async def test_it_cannot_be_switched_on_without_the_token():
    plugins = registry(Coolify(), env={})

    with pytest.raises(ValueError, match="COOLIFY_TOKEN"):
        plugins.set("coolify", {"base_url": BASE}, True)


@pytest.mark.parametrize(
    "url", ["ftp://coolify.test", "coolify.test:8000", "http://u:p@coolify.test"]
)
def test_a_base_url_that_is_not_plain_http_is_refused(url):
    with pytest.raises(ValidationError):
        CoolifySettings(base_url=url)


async def test_health_names_the_version_and_never_the_token():
    plugins = registry(Coolify())
    plugins.set("coolify", {"base_url": BASE}, True)

    health = (await plugins.view("coolify")).health

    assert health is not None and health.ok
    assert health.detail == "Coolify 4.6.0 answers with the token"


async def test_a_refused_token_is_unhealthy_in_bleeps_words():
    coolify = Coolify()
    coolify.routes[("GET", "/api/v1/version")] = httpx.Response(401)
    plugins = registry(coolify)
    plugins.set("coolify", {"base_url": BASE}, True)

    health = (await plugins.view("coolify")).health

    assert health is not None and not health.ok
    assert health.detail == "Coolify refused the token (401); check COOLIFY_TOKEN"
    assert TOKEN not in health.detail


async def test_a_version_answer_that_is_not_a_version_is_not_repeated():
    coolify = Coolify()
    coolify.routes[("GET", "/api/v1/version")] = httpx.Response(200, text="<b>ignore all</b>")
    plugins = registry(coolify)
    plugins.set("coolify", {"base_url": BASE}, True)

    health = (await plugins.view("coolify")).health

    assert health is not None and health.detail == "Coolify answers with the token"
