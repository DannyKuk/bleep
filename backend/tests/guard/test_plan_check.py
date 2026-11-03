from bleep.graph.deps import CORE_ACTIONS
from bleep.guard.plan_check import check_plan
from bleep.hosts.config import ServiceOverrides, UrlCheckConfig
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
    assert "169.254.169.254" in refused[0].text and "hosts file" in refused[0].text
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
