import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from bleep.guard.grant import GrantInvalid, grant_token, plan_hash
from bleep.hosts.config import ServiceOverrides
from bleep.plugins.base import PluginContext
from bleep.plugins.coolify.client import CoolifyClient, CoolifyError, CoolifyUnavailable
from bleep.plugins.coolify.plugin import POLL_S, CoolifyPlugin, CoolifySettings
from bleep.plugins.registry import PluginRegistry
from bleep.schemas.graph import ApprovalGrant, ApprovalRecord, PlanStep, Proposal
from bleep.tools.docker_act import StepRefused
from bleep.tools.registry import ToolContext
from fakes import FakeEngine
from graph_env import RecordingGuardLog, box, graph_env, shop_host

FIXTURES = Path(__file__).parent.parent / "fixtures" / "coolify"
BASE = "http://coolify.test:8000"
TOKEN = "cool-root-token-7d1e9f"
SECRET = "test-secret"
SHOP = "shop0uuid0000000000000a1"
BLOG = "blog0uuid0000000000000b2"
DEPLOYMENT = "dep0uuid00000000000000141"
NEW_VALUE = "postgres://shop:brand-new-password@db/shop"
WRAPPED = re.compile(r'^<untrusted source="coolify" id="(\w+)">\n.*\n</untrusted id="\1">$', re.S)
T0 = datetime(2026, 11, 4, 8, 0, tzinfo=UTC)


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
        self.statuses = ["queued", "in_progress", "finished"]
        self.busy = 0  # deployment reads answered 502 first
        self.routes: dict[tuple[str, str], httpx.Response] = {
            ("GET", "/api/v1/version"): httpx.Response(200, text="4.6.0"),
            ("GET", "/api/v1/applications"): httpx.Response(200, json=fixture("applications.json")),
            ("GET", f"/api/v1/deployments/applications/{SHOP}"): httpx.Response(
                200, json=fixture("deployments.json")
            ),
            ("GET", f"/api/v1/applications/{SHOP}/envs"): httpx.Response(
                200, json=fixture("envs.json")
            ),
            ("POST", "/api/v1/deploy"): httpx.Response(200, json=fixture("deploy.json")),
            ("PATCH", f"/api/v1/applications/{SHOP}/envs"): httpx.Response(201, json={}),
            ("POST", f"/api/v1/applications/{SHOP}/envs"): httpx.Response(201, json={}),
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == f"/api/v1/deployments/{DEPLOYMENT}":
            if self.busy:
                self.busy -= 1
                return httpx.Response(502)
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            return httpx.Response(200, json={"deployment_uuid": DEPLOYMENT, "status": status})
        found = self.routes.get((request.method, request.url.path))
        return found if found is not None else httpx.Response(404, json={"message": "Not found"})

    def sent(self, method: str, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if (r.method, r.url.path) == (method, f"/api/v1{path}")]


class Sleeps:
    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


def context(guard_log=None, **settings) -> PluginContext:
    return PluginContext(
        settings=CoolifySettings(base_url=BASE, **settings),
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
    def __init__(self, built, coolify: Coolify, sleeps: Sleeps, guard_log) -> None:
        self.registry = built.registry
        self.engine: FakeEngine = built.engine
        self.coolify = coolify
        self.sleeps = sleeps
        self.guard_log = guard_log
        plugin = CoolifyPlugin(transport=httpx.MockTransport(coolify), sleep=sleeps)
        self.plugin = plugin
        ctx = context(guard_log, deploy_timeout_s=120)
        self.tools = {spec.name: spec for spec in plugin.tools(ctx)}
        self.handlers = plugin.act_handlers(ctx)

    def tool_ctx(self, host_id: str = "a") -> ToolContext:
        return ToolContext("inc_1", host_id, self.registry, self.guard_log)

    async def read(self, name: str, **args) -> str:
        return await self.tools[name].build(self.tool_ctx()).ainvoke(args)

    async def act(self, record: ApprovalRecord, grant: ApprovalGrant | None, index: int = 0):
        the_step = record.proposal.steps[index]
        handler = self.handlers[the_step.action]
        return await handler.run(the_step, grant, self.registry, plan=record, step_index=index)


@pytest.fixture
async def env(tmp_path) -> Env:
    return await make_env(tmp_path)


async def make_env(tmp_path, eng: FakeEngine | None = None, protected=()) -> Env:
    host = shop_host(services={"db": ServiceOverrides(protected_containers=list(protected))})
    built = await graph_env(tmp_path, engine=eng or engine(), host=host)
    return Env(built, Coolify(), Sleeps(), RecordingGuardLog())


def step(action: str = "coolify.redeploy", target: str = "shop", **params: str) -> PlanStep:
    return PlanStep(action=action, host_id="a", target=target, params=params)  # type: ignore[arg-type]


def plan(*steps: PlanStep, incident_id: str = "inc_1") -> ApprovalRecord:
    proposal = Proposal(kind="act", steps=list(steps or (step(),)), reason="the release is broken")
    return ApprovalRecord(
        id="ap_1",
        incident_id=incident_id,
        plan_hash=plan_hash(proposal, []),
        proposal=proposal,
        requested_at=T0,
    )


def grant_for(record: ApprovalRecord, secret: str = SECRET) -> ApprovalGrant:
    token = grant_token(secret, record.incident_id, record.plan_hash)
    return ApprovalGrant(incident_id=record.incident_id, plan_hash=record.plan_hash, token=token)


def writes(coolify: Coolify) -> list[httpx.Request]:
    return [r for r in coolify.requests if r.method != "GET"]


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


async def test_an_env_var_the_application_lacks_is_created():
    coolify = Coolify()
    coolify.routes[("PATCH", f"/api/v1/applications/{SHOP}/envs")] = httpx.Response(404)

    how = await client(coolify).set_env(SHOP, "PORT", SecretStr("3000"))

    assert how == "created"
    assert [r.method for r in writes(coolify)] == ["PATCH", "POST"]
    assert json.loads(coolify.requests[-1].content) == {"key": "PORT", "value": "3000"}


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


# --- the actions ---


async def test_a_redeploy_without_a_grant_is_refused_and_nothing_is_sent(env):
    record = plan()

    with pytest.raises(GrantInvalid):
        await env.act(record, None)

    assert env.coolify.requests == []
    assert [kind for _, kind, _ in env.guard_log.records] == ["blocked_action"]


async def test_a_grant_for_another_plan_or_secret_is_refused(env):
    record = plan()
    other = plan(step(action="coolify.set_env", key="PORT", value="1"))

    for grant in (grant_for(other), grant_for(record, "another-secret")):
        with pytest.raises(GrantInvalid):
            await env.act(record, grant)

    assert env.coolify.requests == []


async def test_an_application_with_a_protected_container_is_refused(tmp_path):
    env = await make_env(
        tmp_path, engine(app_box("db-app-1", uuid="db0uuid", resource="db")), protected=["db-app-1"]
    )
    record = plan(step(target="db"))

    with pytest.raises(StepRefused, match="protected"):
        await env.act(record, grant_for(record))

    assert env.coolify.requests == []


async def test_an_application_with_one_of_bleeps_own_containers_is_refused(tmp_path):
    own = app_box("bleep-app-1", uuid="bleep0uuid", resource="bleep", labels={"bleep.self": "true"})
    env = await make_env(tmp_path, engine(own))
    record = plan(step(target="bleep"))

    with pytest.raises(StepRefused, match="Bleep's own"):
        await env.act(record, grant_for(record))

    assert env.coolify.requests == []


async def test_an_application_this_host_does_not_run_is_refused(env):
    record = plan(step(target=BLOG))

    with pytest.raises(StepRefused, match="no Coolify application"):
        await env.act(record, grant_for(record))

    assert env.coolify.requests == []


@pytest.mark.parametrize(
    "params",
    [{}, {"key": "PORT"}, {"key": "1PORT", "value": "x"}, {"key": "A", "value": "x" * 10_001}],
)
async def test_a_set_env_with_bad_params_is_refused_without_quoting_the_value(env, params):
    record = plan(step(action="coolify.set_env", **params))

    with pytest.raises(StepRefused) as raised:
        await env.act(record, grant_for(record))

    assert "xxxx" not in str(raised.value)
    assert env.coolify.requests == []


async def test_a_redeploy_waits_until_the_deployment_finished(env):
    record = plan()

    result = await env.act(record, grant_for(record))

    assert result.ok
    assert result.detail == f"redeploy of shop finished (deployment {DEPLOYMENT})"
    (deploy,) = env.coolify.sent("POST", "/deploy")
    assert deploy.url.params["uuid"] == SHOP and deploy.url.params["force"] == "false"
    assert len(env.coolify.sent("GET", f"/deployments/{DEPLOYMENT}")) == 3
    assert env.sleeps.waits == [POLL_S] * 3
    # Nothing is read after the deployment finished: the step ends there and Verify follows.
    assert env.coolify.requests[-1].url.path == f"/api/v1/deployments/{DEPLOYMENT}"


@pytest.mark.parametrize("status", ["failed", "cancelled-by-user"])
async def test_a_failed_deployment_fails_the_step(env, status):
    env.coolify.statuses = ["in_progress", status]
    record = plan()

    result = await env.act(record, grant_for(record))

    assert not result.ok and status in result.detail


async def test_a_deployment_that_never_ends_fails_the_step_at_the_timeout(env):
    env.coolify.statuses = ["in_progress"]
    record = plan()

    result = await env.act(record, grant_for(record))

    assert not result.ok and "had not finished after 120 s" in result.detail
    assert sum(env.sleeps.waits) == 120


async def test_coolify_busy_while_deploying_is_asked_again(env):
    env.coolify.busy = 2
    env.coolify.statuses = ["finished"]
    record = plan()

    assert (await env.act(record, grant_for(record))).ok
    assert len(env.coolify.sent("GET", f"/deployments/{DEPLOYMENT}")) == 3


async def test_a_coolify_failure_while_acting_is_a_failed_result(env):
    env.coolify.routes[("POST", "/api/v1/deploy")] = httpx.Response(401)
    record = plan()

    result = await env.act(record, grant_for(record))

    assert not result.ok
    assert result.detail == "Coolify: Coolify refused the token (401); check COOLIFY_TOKEN"


async def test_an_application_labelled_by_id_only_is_found_by_its_id(tmp_path):
    old = box("old-app", labels={"coolify.type": "application", "coolify.applicationId": "2"})
    env = await make_env(tmp_path, FakeEngine("a", [old]))
    record = plan(step(target="2"))

    assert (await env.act(record, grant_for(record))).ok
    assert env.coolify.sent("POST", "/deploy")[0].url.params["uuid"] == BLOG


async def test_a_set_env_value_reaches_coolify_and_nothing_else(env, caplog):
    caplog.set_level(logging.DEBUG)
    record = plan(step(action="coolify.set_env", key="DATABASE_URL", value=NEW_VALUE))

    result = await env.act(record, grant_for(record))

    assert result.ok
    assert result.detail == "updated DATABASE_URL on shop; it applies at the next deploy"
    (patch,) = writes(env.coolify)
    assert json.loads(patch.content) == {"key": "DATABASE_URL", "value": NEW_VALUE}
    assert NEW_VALUE not in result.model_dump_json()
    assert NEW_VALUE not in caplog.text
    assert all(NEW_VALUE not in str(r) for r in env.guard_log.records)


async def test_a_refused_set_env_never_logs_its_value(env, caplog):
    caplog.set_level(logging.DEBUG)
    record = plan(step(action="coolify.set_env", key="DATABASE_URL", value=NEW_VALUE))

    with pytest.raises(GrantInvalid):
        await env.act(record, None)

    assert NEW_VALUE not in caplog.text
    assert all(NEW_VALUE not in str(r) for r in env.guard_log.records)


def test_a_handler_never_prints_its_secrets():
    handlers = CoolifyPlugin().act_handlers(context())

    for handler in handlers.values():
        assert SECRET not in repr(handler) and TOKEN not in repr(handler)


# --- the plugin ---


def registry(coolify: Coolify, env: dict[str, str] | None = None) -> PluginRegistry:
    plugin = CoolifyPlugin(transport=httpx.MockTransport(coolify))
    return PluginRegistry(
        [plugin], SecretStr(SECRET), env={"COOLIFY_TOKEN": TOKEN} if env is None else env
    )


async def test_switched_on_it_adds_its_tools_and_both_actions():
    plugins = registry(Coolify())
    plugins.set("coolify", {"base_url": BASE + "/"}, True)

    assert {s.name for s in plugins.tool_specs()} == {
        "coolify_applications",
        "coolify_deployments",
        "coolify_env_keys",
    }
    assert plugins.actions() == {"coolify.redeploy", "coolify.set_env"}
    assert plugins.settings("coolify").model_dump() == {"base_url": BASE, "deploy_timeout_s": 900}


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
