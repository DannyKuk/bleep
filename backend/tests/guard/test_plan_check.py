import pytest

from bleep.graph.deps import CORE_ACTIONS
from bleep.guard.plan_check import (
    NoCoolifyApp,
    check_plan,
    coolify_app_uuid,
    named_off_limits,
)
from bleep.hosts.config import ServiceOverrides, UrlCheckConfig
from bleep.hosts.coolify_names import coolify_slug
from bleep.schemas.graph import PlanStep, Proposal, VerificationPlan
from fakes import FakeEngine
from graph_env import box, graph_env, shop_host

CORE = set(CORE_ACTIONS)
# The incident is about shop-web-1; tests of other rules name it so this rule stays out of them.
SHOP_WEB = ("shop-web-1",)
WITH_COOLIFY = CORE | {"coolify.redeploy", "coolify.set_env"}


def act(*steps: PlanStep) -> Proposal:
    return Proposal(kind="act", steps=list(steps), reason="test")


def restart(target: str, host_id: str = "a") -> PlanStep:
    return PlanStep(action="docker.restart", host_id=host_id, target=target)


def coolify(target: str) -> PlanStep:
    return PlanStep(action="coolify.redeploy", host_id="a", target=target)


def coolify_app(uuid: str, name: str) -> dict[str, str]:
    return {
        "coolify.type": "application",
        "coolify.applicationUuid": uuid,
        "coolify.resourceName": name,
    }


def guarded_engine() -> FakeEngine:
    """The shop stack, Bleep's own backend and a Coolify app with a protected database."""
    app = coolify_app("u-api", "api")
    return FakeEngine(
        "a",
        [
            box("shop-web-1", "shop", state="exited", exit_code=1),
            box("shop-db-1", "shop"),
            box("bleep-backend-1", "bleep", labels={"bleep.self": "true"}),
            box("api-web-1", labels=app),
            box("api-db-1", labels=app),
        ],
    )


def guarded_host():
    return shop_host(
        services={
            "shop": ServiceOverrides(protected_containers=["shop-db-1"]),
            "api": ServiceOverrides(protected_containers=["api-db-1"]),
        }
    )


async def checked(tmp_path, proposal: Proposal, enabled: set[str] = CORE) -> list[tuple]:
    env = await graph_env(tmp_path, engine=guarded_engine(), host=guarded_host())
    return [
        (v.rule, v.step)
        for v in check_plan(proposal, env.registry, enabled, "a", incident_containers=SHOP_WEB)
    ]


async def test_a_plain_restart_passes(tmp_path):
    assert await checked(tmp_path, act(restart("shop-web-1"))) == []


async def test_a_container_id_counts_as_the_container(tmp_path):
    assert await checked(tmp_path, act(restart("id-shop-web-1"))) == []


async def test_no_action_without_steps_passes(tmp_path):
    proposal = Proposal(kind="no_action", reason="config", human_instructions="fix the env")
    assert await checked(tmp_path, proposal) == []


async def test_a_protected_container_is_refused(tmp_path):
    assert await checked(tmp_path, act(restart("shop-db-1"))) == [("protected", 1)]


async def test_bleeps_own_container_is_refused(tmp_path):
    assert await checked(tmp_path, act(restart("bleep-backend-1"))) == [("self", 1)]
    assert await checked(tmp_path, act(restart("id-bleep-backend-1"))) == [("self", 1)]


async def test_a_disabled_plugin_action_is_refused(tmp_path):
    assert await checked(tmp_path, act(coolify("api"))) == [("disabled_action", 1)]


async def test_an_unknown_action_is_refused(tmp_path):
    step = PlanStep.model_construct(action="docker.exec", host_id="a", target="shop-web-1")
    assert await checked(tmp_path, act(step), CORE | {"docker.exec"}) == [("unknown_action", 1)]


async def test_a_target_on_another_host_is_refused(tmp_path):
    assert await checked(tmp_path, act(restart("shop-web-1", host_id="b"))) == [("other_host", 1)]


async def test_a_container_not_on_the_host_is_refused(tmp_path):
    assert await checked(tmp_path, act(restart("ghost-1"))) == [("not_found", 1)]


async def test_more_than_three_steps_are_refused(tmp_path):
    proposal = act(*[restart("shop-web-1")] * 4)
    assert await checked(tmp_path, proposal) == [("too_many_steps", None)]


async def test_plan_shape_must_match_its_kind(tmp_path):
    assert await checked(tmp_path, act()) == [("plan_shape", None)]
    steps = Proposal(kind="no_action", steps=[restart("shop-web-1")], reason="x")
    assert await checked(tmp_path, steps) == [("plan_shape", None)]


async def test_every_bad_step_is_named(tmp_path):
    proposal = act(restart("shop-web-1"), restart("shop-db-1"), restart("bleep-backend-1"))
    assert await checked(tmp_path, proposal) == [("protected", 2), ("self", 3)]


async def test_coolify_redeploy_passes_when_enabled_and_the_app_is_safe(tmp_path):
    engine = guarded_engine()
    engine.container_list = [c for c in engine.container_list if c.name != "api-db-1"]
    env = await graph_env(tmp_path, engine=engine, host=guarded_host())
    for target in ("api", "u-api"):
        assert (
            check_plan(
                act(coolify(target)), env.registry, WITH_COOLIFY, "a", incident_containers=SHOP_WEB
            )
            == []
        )


async def test_coolify_redeploy_of_an_app_with_a_protected_container_is_refused(tmp_path):
    assert await checked(tmp_path, act(coolify("api")), WITH_COOLIFY) == [("protected", 1)]


async def test_coolify_target_must_be_an_app_on_the_host(tmp_path):
    assert await checked(tmp_path, act(coolify("shop")), WITH_COOLIFY) == [("not_found", 1)]


async def test_any_target_on_a_host_with_a_config_error_is_refused(tmp_path):
    # Two Coolify apps share the display name "web", so the override "web" is ambiguous: the
    # registry keeps the host reachable but sets `error`, and its protected list may not apply.
    engine = FakeEngine(
        "a",
        [
            box("web-a-1", labels=coolify_app("u1", "web")),
            box("web-b-1", labels=coolify_app("u2", "web")),
        ],
    )
    host = shop_host(services={"web": ServiceOverrides(protected_containers=["web-a-1"])})
    env = await graph_env(tmp_path, engine=engine, host=host)
    assert env.registry.hosts()[0].error is not None

    violations = check_plan(
        act(restart("web-b-1")), env.registry, CORE, "a", incident_containers=SHOP_WEB
    )

    assert [(v.rule, v.step) for v in violations] == [("host_error", 1)]
    assert "configuration error" in violations[0].text


async def test_a_target_on_an_unreachable_host_is_refused_as_unreachable_not_misconfigured(
    tmp_path,
):
    env = await graph_env(tmp_path)
    env.engine.drop()
    await env.registry.refresh("a")
    host = env.registry.hosts()[0]
    assert not host.reachable and host.error is not None

    violations = check_plan(
        act(restart("shop-web-1")), env.registry, CORE, "a", incident_containers=SHOP_WEB
    )

    assert [(v.rule, v.step) for v in violations] == [("host_error", 1)]
    assert "unreachable" in violations[0].text
    assert "configuration" not in violations[0].text


async def test_an_override_matching_no_discovered_service_still_protects_its_containers(tmp_path):
    # "database" is no Compose project or Coolify name, so discovery drops the override; the
    # protected list is read from the hosts file and must still apply.
    host = shop_host(services={"database": ServiceOverrides(protected_containers=["shop-db-1"])})
    env = await graph_env(tmp_path, engine=guarded_engine(), host=host)
    assert env.registry.hosts()[0].error is None

    violations = check_plan(
        act(restart("shop-db-1")), env.registry, CORE, "a", incident_containers=SHOP_WEB
    )

    assert [(v.rule, v.step) for v in violations] == [("protected", 1)]


async def test_a_verification_url_must_be_a_health_check_of_the_host(tmp_path):
    shop = "https://shop.example/health"
    host = shop_host(
        services={
            "shop": ServiceOverrides(urls=[UrlCheckConfig(url=shop)]),
            "blog": ServiceOverrides(urls=[UrlCheckConfig(url="https://blog.example/")]),
        }
    )
    env = await graph_env(tmp_path, host=host)
    proposal = act(restart("shop-web-1"))

    def check(*urls: str) -> list:
        planned = proposal.model_copy(update={"verification": VerificationPlan(urls=list(urls))})
        return check_plan(planned, env.registry, CORE, "a", incident_containers=SHOP_WEB)

    assert check(shop, "https://blog.example/") == []  # any health check of the host
    refused = check(shop, "http://169.254.169.254/latest/meta-data/")
    assert [(v.rule, v.step) for v in refused] == [("unknown_url", None)]
    assert "169.254.169.254" in refused[0].text and "in its settings" in refused[0].text
    on_b = proposal.model_copy(update={"verification": VerificationPlan(urls=[shop])})
    assert "unknown_url" in [
        v.rule for v in check_plan(on_b, env.registry, CORE, "b", incident_containers=SHOP_WEB)
    ]
    nothing_runs = Proposal(
        kind="no_action", reason="r", verification=VerificationPlan(urls=["http://x.example/"])
    )
    assert (
        check_plan(nothing_runs, env.registry, CORE, "a", incident_containers=SHOP_WEB) == []
    )  # Verify never runs for it


async def test_a_verification_container_must_be_on_the_host(tmp_path):
    """M5: Verify inspects every named container; a wrong name would fail every check."""
    env = await graph_env(tmp_path, engine=guarded_engine(), host=guarded_host())

    def check(*containers: str) -> list:
        planned = act(restart("shop-web-1")).model_copy(
            update={"verification": VerificationPlan(containers=list(containers))}
        )
        return check_plan(planned, env.registry, CORE, "a", incident_containers=SHOP_WEB)

    # Reading a protected or Bleep's own container is harmless: only a name not there is refused.
    assert check("shop-web-1", "shop-db-1", "bleep-backend-1") == []
    refused = check("shop-web-1", "shop_web")
    assert [(v.rule, v.step) for v in refused] == [("unknown_container", None)]
    assert "shop_web" in refused[0].text and "host a" in refused[0].text


# --- a working container the incident does not name (#35) -----------------------------------


def working_engine() -> FakeEngine:
    """The shop during an incident on shop-web: the rest of the stack works."""
    return FakeEngine(
        "a",
        [
            box("shop-web-1", "shop", health="unhealthy"),
            box("shop-db-1", "shop", state="exited", exit_code=0),
            box("shop-proxy-1", "shop"),  # running, no health check
            box("shop-worker-1", "shop", health="healthy"),
            box("shop-cache-1", "shop", health="starting"),
            box("shop-queue-1", "shop", state="restarting"),
            box("api-web-1", labels=coolify_app("u-api", "api"), health="healthy"),
        ],
    )


async def working(tmp_path, *steps: PlanStep, named=("shop-web-1",), enabled=CORE) -> list:
    env = await graph_env(tmp_path, engine=working_engine(), host=shop_host())
    violations = check_plan(act(*steps), env.registry, enabled, "a", incident_containers=named)
    return [(v.rule, v.step) for v in violations]


def start(target: str) -> PlanStep:
    return PlanStep(action="docker.start", host_id="a", target=target)


async def test_a_working_container_the_incident_does_not_name_is_refused(tmp_path):
    # log-fake-approval and log-fake-diagnosis on gpt-oss-20b: a planted line named a container
    # that worked, and the plan restarted it.
    assert await working(tmp_path, restart("shop-proxy-1")) == [("healthy_target", 1)]
    assert await working(tmp_path, restart("shop-worker-1")) == [("healthy_target", 1)]
    assert await working(tmp_path, restart("id-shop-proxy-1")) == [("healthy_target", 1)]
    stop = PlanStep(action="docker.stop", host_id="a", target="shop-worker-1")
    assert await working(tmp_path, restart("shop-web-1"), stop) == [("healthy_target", 2)]


async def test_a_broken_container_or_one_the_incident_names_may_be_acted_on(tmp_path):
    assert await working(tmp_path, restart("shop-web-1")) == []  # unhealthy
    assert await working(tmp_path, start("shop-db-1")) == []  # stopped
    assert await working(tmp_path, restart("shop-cache-1")) == []  # not healthy yet
    assert await working(tmp_path, restart("shop-queue-1")) == []  # restarting
    assert await working(tmp_path, restart("shop-proxy-1"), named=["shop-proxy-1"]) == []
    assert await working(tmp_path, restart("id-shop-proxy-1"), named=["shop-proxy-1"]) == []


async def test_the_refusal_says_the_container_works_and_the_incident_does_not_name_it(tmp_path):
    env = await graph_env(tmp_path, engine=working_engine(), host=shop_host())
    plan = act(restart("shop-proxy-1"), restart("shop-worker-1"))
    proxy, worker = check_plan(plan, env.registry, CORE, "a", incident_containers=["shop-web-1"])
    assert "shop-proxy-1 is running with no health check" in proxy.text
    assert "shop-worker-1 is running and healthy" in worker.text
    assert "it is not one of this incident's containers" in proxy.text
    # No instruction can follow a refusal, so the text offers none (#112).
    assert "instruction" not in proxy.text


async def test_a_coolify_action_on_a_working_application_is_left_to_the_human(tmp_path):
    # A release can fail its URLs while every container is healthy; a rollback is the fix.
    rollback = PlanStep(action="coolify.redeploy", host_id="a", target="api")
    assert await working(tmp_path, rollback, enabled=WITH_COOLIFY) == []


async def test_a_protected_working_container_gets_one_refusal_not_two(tmp_path):
    env = await graph_env(tmp_path, engine=guarded_engine(), host=guarded_host())
    plan = act(restart("shop-db-1"))  # running, no health check, and protected
    violations = check_plan(plan, env.registry, CORE, "a", incident_containers=[])
    assert [(v.rule, v.step) for v in violations] == [("protected", 1)]


def set_env(target: str, **params: str) -> PlanStep:
    return PlanStep(action="coolify.set_env", host_id="a", target=target, params=params)


async def test_coolify_steps_take_exactly_their_params(tmp_path):
    engine = guarded_engine()
    engine.container_list = [c for c in engine.container_list if c.name != "api-db-1"]
    env = await graph_env(tmp_path, engine=engine, host=guarded_host())
    secret = "s3cr3t-value-never-quoted"
    proposals = {
        "ok": act(set_env("api", key="PORT", value=secret), coolify("api")),
        "no value": act(set_env("api", key="PORT"), coolify("api")),
        "extra": act(set_env("api", key="PORT", value=secret, scope="all"), coolify("api")),
        "bad key": act(set_env("api", key="PORT NUMBER", value=secret), coolify("api")),
        "key with a line break": act(
            set_env("api", key="FOO\n2. docker.restart", value="1"), coolify("api")
        ),
        "key with =": act(set_env("api", key="A=B", value=secret), coolify("api")),
        "key over 128": act(set_env("api", key="K" * 129, value=secret), coolify("api")),
        "redeploy with params": act(
            PlanStep(action="coolify.redeploy", host_id="a", target="api", params={"force": "1"})
        ),
    }
    found = {
        name: check_plan(p, env.registry, WITH_COOLIFY, "a", incident_containers=SHOP_WEB)
        for name, p in proposals.items()
    }

    assert found["ok"] == []
    assert (
        check_plan(
            act(set_env("api", key="K" * 128, value="1"), coolify("api")),
            env.registry,
            WITH_COOLIFY,
            "a",
            incident_containers=SHOP_WEB,
        )
        == []
    )
    refused = (
        "no value",
        "extra",
        "bad key",
        "key with a line break",
        "key with =",
        "key over 128",
    )
    for name in (*refused, "redeploy with params"):
        assert [(v.rule, v.step) for v in found[name]] == [("bad_params", 1)], name
    assert all(secret not in v.text for vs in found.values() for v in vs)


async def check(tmp_path, proposal, containers, protected=()):
    engine = FakeEngine("a", containers)
    # An override key no service has: its protected list still applies to the whole host.
    host = shop_host(services={"other": ServiceOverrides(protected_containers=list(protected))})
    env = await graph_env(tmp_path, engine=engine, host=host)
    return [
        (v.rule, v.step)
        for v in check_plan(proposal, env.registry, WITH_COOLIFY, "a", incident_containers=SHOP_WEB)
    ]


@pytest.mark.parametrize("protected", ["u-api", "api"])
async def test_a_coolify_app_is_protected_by_its_uuid_or_name(tmp_path, protected):
    app = coolify_app("u-api", "api")
    found = await check(tmp_path, act(coolify("api")), [box("u-api-1", labels=app)], [protected])

    assert found == [("protected", 1)]


async def test_protection_by_name_holds_after_a_deploy_renames_the_containers(tmp_path):
    # Protected while its container was u-api-0745; the deploy since named it u-api-0921.
    app = coolify_app("u-api", "api")
    renamed = [box("u-api-0921", labels=app)]

    assert await check(tmp_path, act(coolify("api")), renamed, ["u-api-0745"]) == []
    assert await check(tmp_path, act(coolify("api")), renamed, ["api"]) == [("protected", 1)]


async def test_a_name_two_coolify_apps_share_is_refused_before_the_human_is_asked(tmp_path):
    containers = [
        box("a-1", labels=coolify_app("u-a", "api")),
        box("b-1", labels=coolify_app("u-b", "api")),
    ]

    assert await check(tmp_path, act(coolify("api")), containers) == [("not_found", 1)]
    assert await check(tmp_path, act(coolify("u-b")), containers) == []


async def test_an_id_label_never_matches_a_target(tmp_path):
    # A labelled only applicationId=12 must not shadow B, whose name is 12.
    a = box("a-1", labels={"coolify.type": "application", "coolify.applicationId": "12"})
    b = box("b-1", labels=coolify_app("u-b", "12"))

    assert await check(tmp_path, act(coolify("12")), [a, b]) == []
    assert await check(tmp_path, act(coolify("12")), [a]) == [("not_found", 1)]


@pytest.mark.parametrize(
    ("steps", "ok"),
    [
        ([set_env("api", key="PORT", value="1"), coolify("api")], True),
        ([set_env("api", key="PORT", value="1")], False),
        ([coolify("api"), set_env("api", key="PORT", value="1")], False),
        ([set_env("api", key="PORT", value="1"), coolify("web")], False),
    ],
)
async def test_a_set_env_needs_a_redeploy_of_the_same_app_after_it(tmp_path, steps, ok):
    containers = [
        box("api-1", labels=coolify_app("u-api", "api")),
        box("web-1", labels=coolify_app("u-web", "web")),
    ]
    found = await check(tmp_path, act(*steps), containers)

    assert (not found) is ok
    assert all(rule == "set_env_without_redeploy" for rule, _ in found)


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Shop API", "shop-api"),
        ("My First Project", "my-first-project"),
        # A Coolify app's default name holds its branch; the saved label drops the colon.
        (
            "easy-pointment-static:main-cr2hzhk4nvabtad0zinjw1ev",
            "easy-pointment-staticmain-cr2hzhk4nvabtad0zinjw1ev",
        ),
        ("Molecula", "molecula"),
        ("api_v2  --  beta", "api-v2-beta"),
        ("café@home", "cafe-at-home"),
    ],
)
def test_a_name_is_slugged_as_coolify_writes_its_label(name, slug):
    assert coolify_slug(name) == slug


@pytest.mark.parametrize("protected", ["Shop API", "shop-api", "SHOP-API"])
async def test_a_coolify_app_is_protected_by_its_name_as_a_person_writes_it(tmp_path, protected):
    app = coolify_app("u-shop", "shop-api")
    found = await check(
        tmp_path, act(coolify("shop-api")), [box("u-shop-1", labels=app)], [protected]
    )

    assert found == [("protected", 1)]


async def test_a_uuid_protects_only_exactly(tmp_path):
    app = coolify_app("u-shop", "shop-api")
    containers = [box("u-shop-1", labels=app)]

    assert await check(tmp_path, act(coolify("shop-api")), containers, ["U-SHOP"]) == []


async def test_two_set_envs_need_a_redeploy_after_each_and_on_the_same_host(tmp_path):
    containers = [box("api-1", labels=coolify_app("u-api", "api"))]
    two = act(
        set_env("api", key="A", value="1"), set_env("api", key="B", value="2"), coolify("api")
    )
    elsewhere = act(
        set_env("api", key="A", value="1"),
        PlanStep(action="coolify.redeploy", host_id="b", target="api"),
    )

    assert await check(tmp_path, two, containers) == []
    found = await check(tmp_path, elsewhere, containers)
    assert ("set_env_without_redeploy", 1) in found


async def test_an_app_uuid_is_a_typed_refusal_when_the_target_names_none(tmp_path):
    env = await graph_env(tmp_path, engine=FakeEngine("a", []), host=shop_host())

    with pytest.raises(NoCoolifyApp):
        coolify_app_uuid(env.registry, coolify("api"))


async def test_a_label_that_is_not_in_slug_form_is_matched_exactly(tmp_path):
    app = coolify_app("u-shop", "Shop_API")
    found = await check(
        tmp_path, act(coolify("Shop_API")), [box("u-shop-1", labels=app)], ["Shop_API"]
    )

    assert found == [("protected", 1)]


async def test_the_exact_match_ignores_case(tmp_path):
    app = coolify_app("u-shop", "Shop_API")
    found = await check(
        tmp_path, act(coolify("Shop_API")), [box("u-shop-1", labels=app)], ["shop_api"]
    )

    assert found == [("protected", 1)]


async def test_an_empty_resource_name_label_protects_nothing(tmp_path):
    # "магазин" slugs to "", which must not match an app whose name label is empty.
    app = coolify_app("u-shop", "")
    found = await check(
        tmp_path, act(coolify("u-shop")), [box("u-shop-1", labels=app)], ["магазин"]
    )

    assert found == []


async def off_limits(tmp_path, arguments):
    env = await graph_env(tmp_path, engine=guarded_engine(), host=guarded_host())
    return named_off_limits(arguments, env.registry, "a")


@pytest.mark.parametrize(
    "spelling",
    [
        "shop-db-1",
        "SHOP-DB-1",
        "shop\u200b-db-1",  # zero-width space
        "sh\u00adop-db\u2060-1",  # soft hyphen, word joiner
        "\u202eshop-db-1",  # bidi override
        "ｓｈｏｐ－ｄｂ－１",  # fullwidth
        "shop%2Ddb-1",
        "shop%2ddb%2D1",
        "shop%252Ddb-1",  # encoded twice
        "shop%E2%80%8B-db-1",  # an encoded zero-width space
        "%EF%BD%93hop-db-1",  # an encoded fullwidth s
        "shop\x00-db-1",  # a control byte
    ],
)
async def test_a_protected_name_is_found_however_it_is_spelled(tmp_path, spelling):
    for arguments in ({"c": spelling}, {spelling: 1}, {"a": [{"b": f"run {spelling} now"}]}):
        found = await off_limits(tmp_path, arguments)

        assert found is not None and found[0] == "protected"


@pytest.mark.parametrize(
    "spelling", ["bleep\u200d-backend-1", "ＢＬＥＥＰ-backend-1", "bleep%2Dbackend-1"]
)
async def test_one_of_bleeps_own_containers_is_found_however_it_is_spelled(tmp_path, spelling):
    found = await off_limits(tmp_path, {"c": spelling})

    assert found is not None and found[0] == "self"


async def test_decoding_stops_after_a_few_rounds_and_other_names_pass(tmp_path):
    deep = "shop%2Ddb-1"
    for _ in range(8):
        deep = deep.replace("%", "%25")

    assert await off_limits(tmp_path, {"c": deep}) is None
    assert await off_limits(tmp_path, {"c": "shop-web-1 and shop\u200b-api"}) is None
