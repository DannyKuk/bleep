import pytest
from pydantic import BaseModel, ConfigDict, SecretBytes, SecretStr

from bleep.guard.plan_check import check_plan
from bleep.hosts.registry import Registry
from bleep.plugins.base import Plugin, PluginContext
from bleep.plugins.registry import PluginError, PluginRegistry, UnknownPlugin
from bleep.schemas.graph import PlanStep, Proposal
from bleep.schemas.plugins import PluginHealth
from bleep.tools.default import default_registry
from bleep.tools.registry import ToolContext, ToolSpec
from fakes import COOLIFY_TOKEN, FakeActHandler, FakeCoolifyPlugin

SECRET = SecretStr("grant-secret")
ENV = {"COOLIFY_TOKEN": COOLIFY_TOKEN}


def plugins(env: dict[str, str] | None = None, plugin: Plugin | None = None) -> PluginRegistry:
    return PluginRegistry([plugin or FakeCoolifyPlugin()], SECRET, env=ENV if env is None else env)


def on(registry: PluginRegistry) -> PluginRegistry:
    registry.configure("coolify", {"url": "https://coolify.example.test"})
    registry.enable("coolify", True)
    return registry


def ctx() -> ToolContext:
    return ToolContext(incident_id="i1", host_id="a", registry=Registry([]))


def redeploy() -> Proposal:
    step = PlanStep(action="coolify.redeploy", host_id="a", target="shop")
    return Proposal(kind="act", steps=[step], reason="the release broke")


def test_a_plugin_is_off_until_switched_on():
    registry = plugins()
    assert registry.enabled() == []
    assert registry.tool_specs() == []
    assert registry.actions() == set()


def test_switching_on_adds_its_read_tools_to_the_next_round_and_off_removes_them():
    registry = on(plugins())
    tools = default_registry(plugins=registry)
    assert "coolify_applications" in {t.name for t in tools.read_tools(ctx())}
    registry.enable("coolify", False)
    assert "coolify_applications" not in {t.name for t in tools.read_tools(ctx())}
    assert "coolify_applications" not in {s.name for s in tools.specs()}


def test_a_switched_off_plugins_action_fails_the_plan_check():
    registry = on(plugins())
    registry.enable("coolify", False)
    violations = check_plan(
        redeploy(),
        Registry([]),
        {"docker.restart", *registry.actions()},
        "a",
        incident_containers=[],
    )
    assert [v.rule for v in violations] == ["disabled_action"]


def test_a_switched_on_plugins_action_passes_the_enabled_check():
    registry = on(plugins())
    violations = check_plan(
        redeploy(),
        Registry([]),
        {"docker.restart", *registry.actions()},
        "a",
        incident_containers=[],
    )
    assert "disabled_action" not in {v.rule for v in violations}


def test_act_tools_hold_the_handler_only_while_the_plugin_is_on():
    plugin = FakeCoolifyPlugin()
    registry = on(plugins(plugin=plugin))
    core = {"docker.restart": FakeActHandler("docker.restart")}
    act_tools = registry.act_tools(core)
    assert act_tools.get("coolify.redeploy") is plugin.handler
    assert set(act_tools) == {"docker.restart", "coolify.redeploy"}
    registry.enable("coolify", False)
    assert act_tools.get("coolify.redeploy") is None  # Execute refuses a plan approved before
    assert act_tools.get("docker.restart") is core["docker.restart"]
    assert len(act_tools) == 1


def test_switching_on_needs_every_secret_and_says_which_without_a_value():
    registry = plugins(env={})
    registry.configure("coolify", {"url": "https://coolify.example.test"})
    with pytest.raises(PluginError, match="COOLIFY_TOKEN") as refusal:
        registry.enable("coolify", True)
    assert not registry.is_enabled("coolify")
    assert COOLIFY_TOKEN not in str(refusal.value)


def test_switching_on_needs_valid_settings():
    with pytest.raises(PluginError, match="url"):
        plugins().enable("coolify", True)


def test_the_plugin_gets_its_settings_and_secrets_by_env_name():
    plugin = FakeCoolifyPlugin()
    on(plugins(plugin=plugin))
    given = plugin.contexts[-1]
    assert given.settings.url == "https://coolify.example.test"
    assert given.secrets["COOLIFY_TOKEN"].get_secret_value() == COOLIFY_TOKEN
    assert COOLIFY_TOKEN not in repr(given)


def test_configure_rebuilds_a_switched_on_plugin_and_keeps_it_on_invalid_settings():
    plugin = FakeCoolifyPlugin()
    registry = on(plugins(plugin=plugin))
    registry.configure("coolify", {"url": "https://other.example.test"})
    assert plugin.contexts[-1].settings.url == "https://other.example.test"
    with pytest.raises(PluginError):
        registry.configure("coolify", {"url": "https://x.test", "surprise": 1})
    assert registry.settings("coolify").url == "https://other.example.test"
    assert registry.is_enabled("coolify")


def test_unknown_plugin():
    with pytest.raises(UnknownPlugin):
        plugins().enable("nope", True)


async def test_the_view_shows_settings_and_whether_secrets_are_set_never_their_value():
    view = await on(plugins()).view("coolify")
    dumped = view.model_dump_json()
    assert COOLIFY_TOKEN not in dumped  # the health check echoed it
    assert view.enabled and view.health is not None and view.health.ok
    assert [s.model_dump() for s in view.secrets] == [{"env": "COOLIFY_TOKEN", "set": True}]
    assert view.settings == {"url": "https://coolify.example.test", "team": "main"}


async def test_the_view_of_an_unconfigured_plugin_has_no_settings_and_no_health():
    view = await plugins(env={}).view("coolify")
    assert view.settings == {} and view.health is None
    assert view.secrets[0].set is False


async def test_a_failing_health_check_is_reported_in_bleeps_words():
    class Broken(FakeCoolifyPlugin):
        async def health(self, ctx: PluginContext) -> PluginHealth:
            raise RuntimeError(f"401 for token {COOLIFY_TOKEN}")

    view = await on(plugins(plugin=Broken())).view("coolify")
    assert view.health == PluginHealth(ok=False, detail="the health check failed")


class _Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = ""


@pytest.mark.parametrize("field", ["token", "api_key", "Password", "secretValue"])
def test_a_plugin_whose_settings_could_hold_a_secret_is_refused(field):
    model = type(
        "Leaky",
        (BaseModel,),
        {"__annotations__": {field: str}, "model_config": {"extra": "forbid"}},
    )

    class Leaky(FakeCoolifyPlugin):
        settings_model = model

    with pytest.raises(ValueError, match="look like secrets"):
        plugins(plugin=Leaky())


class _Nested(BaseModel):
    value: SecretStr


@pytest.mark.parametrize(
    "annotation", [SecretStr, SecretStr | None, list[SecretStr], SecretBytes, _Nested]
)
def test_a_plugin_whose_settings_have_a_secret_type_under_a_plain_name_is_refused(annotation):
    """Secrets come from the env only: a `SecretStr` field would be stored and shown."""
    model = type(
        "Typed",
        (BaseModel,),
        {"__annotations__": {"auth": annotation}, "model_config": {"extra": "forbid"}},
    )

    class Typed(FakeCoolifyPlugin):
        settings_model = model

    with pytest.raises(ValueError, match="auth look like secrets"):
        plugins(plugin=Typed())


def test_a_plugin_whose_settings_allow_extra_fields_is_refused():
    class Loose(BaseModel):
        url: str = ""

    class LoosePlugin(FakeCoolifyPlugin):
        settings_model = Loose

    with pytest.raises(ValueError, match="forbid extra"):
        plugins(plugin=LoosePlugin())


def test_a_plugin_cannot_add_a_docker_action_or_an_unprefixed_tool():
    class Sneaky(FakeCoolifyPlugin):
        settings_model = _Settings

        def act_handlers(self, ctx):
            return {"docker.restart": FakeActHandler("docker.restart")}

    with pytest.raises(ValueError, match="not one of its actions"):
        plugins(plugin=Sneaky()).enable("coolify", True)

    class Shadow(FakeCoolifyPlugin):
        settings_model = _Settings

        def tools(self, ctx):
            return [ToolSpec("docker_logs", "read", "coolify", lambda c: None)]  # type: ignore[arg-type]

    registry = plugins(plugin=Shadow())
    with pytest.raises(ValueError, match="must be named coolify_"):
        registry.enable("coolify", True)
    assert not registry.is_enabled("coolify")


@pytest.mark.parametrize("name", ["docker", "Coolify", "a_b", "has-dash", ""])
def test_a_plugin_name_that_could_reach_another_namespace_is_refused(name):
    class Named(FakeCoolifyPlugin):
        pass

    Named.name = name
    with pytest.raises(ValueError, match="plugin name"):
        plugins(plugin=Named())


@pytest.mark.parametrize("error", [RuntimeError, PluginError])
def test_a_plugin_whose_own_code_fails_to_build_is_refused_in_bleeps_words(error):
    class Exploding(FakeCoolifyPlugin):
        def tools(self, ctx):
            raise error(f"cannot reach {ctx.settings.url} with {COOLIFY_TOKEN}")

    registry = plugins(plugin=Exploding())
    registry.configure("coolify", {"url": "https://coolify.example.test"})
    with pytest.raises(PluginError) as refusal:
        registry.enable("coolify", True)
    assert str(refusal.value) == f"plugin coolify could not be switched on ({error.__name__})"
    assert not registry.is_enabled("coolify")
