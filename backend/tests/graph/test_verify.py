import httpx

from bleep.graph.nodes import verify
from bleep.hosts.config import ServiceOverrides, UrlCheckConfig
from bleep.schemas.graph import PlanStep, Proposal, VerificationPlan, VerificationResult
from bleep.schemas.incidents import Subject
from fakes import FakeEngine
from graph_env import box, graph_env, shop_host

URL = "https://shop.example/health"


class Sleeps:
    """The injected clock: records every wait and runs a hook after it, so a test can change
    the world while "time passes"."""

    def __init__(self, hook=None) -> None:
        self.waits: list[float] = []
        self.hook = hook

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)
        if self.hook is not None:
            self.hook(len(self.waits))


class Replies:
    def __init__(self, *statuses: int) -> None:
        self.statuses = list(statuses)
        self.seen = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        status = self.statuses[min(self.seen, len(self.statuses) - 1)]
        self.seen += 1
        return httpx.Response(status)


class Notices:
    def __init__(self) -> None:
        self.results: list[VerificationResult] = []

    async def verification(self, incident, result) -> None:
        self.results.append(result)


def healthy_engine() -> FakeEngine:
    return FakeEngine(
        "a",
        [box("shop-web-1", "shop", health="healthy"), box("shop-db-1", "shop")],
    )


async def setup(tmp_path, replies, plan=None, sleeps=None, engine=None, urls=True):
    host = shop_host(
        services={"shop": ServiceOverrides(urls=[UrlCheckConfig(url=URL)] if urls else [])}
    )
    env = await graph_env(tmp_path, engine=engine or healthy_engine(), host=host)
    env.deps.sleep = sleeps or Sleeps()
    env.deps.http = httpx.AsyncClient(transport=httpx.MockTransport(replies))
    env.deps.notifier = Notices()
    subject = Subject(kind="service", host_id="a", service_id=env.service_id("shop"))
    incident = await env.open(subject)
    proposal = Proposal(
        kind="act",
        steps=[PlanStep(action="docker.restart", host_id="a", target="shop-web-1")],
        reason="r",
        verification=plan or VerificationPlan(containers=["shop-web-1"], urls=[URL]),
    )
    state = {"incident_id": incident.id, "subject": subject, "proposal": proposal}
    return env, state


async def run(env, state):
    update = await verify.make(env.deps)(state)
    return update, verify.route({**state, **update})


async def test_a_healthy_container_with_a_failing_url_is_not_ok(tmp_path):
    env, state = await setup(tmp_path, Replies(503))

    update, _ = await run(env, state)

    result = update["verification"]
    assert not result.ok
    by_kind = {c.kind: c for c in result.checks}
    assert by_kind["container"].ok and not by_kind["url"].ok
    assert "503" in by_kind["url"].detail


async def test_it_waits_20_s_then_rechecks_three_times_10_s_apart(tmp_path):
    sleeps = Sleeps()
    env, state = await setup(tmp_path, Replies(503), sleeps=sleeps)

    update, _ = await run(env, state)

    assert sleeps.waits == [20, 10, 10]
    assert update["verification"].attempt == 3


async def test_ok_on_the_second_check_resolves_and_stops_checking(tmp_path):
    replies = Replies(503, 200, 200)
    sleeps = Sleeps()
    env, state = await setup(tmp_path, replies, sleeps=sleeps)

    update, route = await run(env, state)

    assert update["verification"].ok and update["verification"].attempt == 2
    assert update["outcome"] == "resolved"
    assert replies.seen == 2 and sleeps.waits == [20, 10]
    assert route == "report"


async def test_a_container_that_is_still_unhealthy_fails_the_check(tmp_path):
    engine = FakeEngine(
        "a", [box("shop-web-1", "shop", health="unhealthy"), box("shop-db-1", "shop")]
    )
    env, state = await setup(tmp_path, Replies(200), engine=engine)

    update, _ = await run(env, state)

    web = update["verification"].checks[0]
    assert (web.target, web.ok, web.detail) == ("shop-web-1", False, "is unhealthy")


async def test_a_stopped_container_fails_even_when_the_url_answers(tmp_path):
    engine = FakeEngine("a", [box("shop-web-1", "shop", state="exited"), box("shop-db-1", "shop")])
    env, state = await setup(tmp_path, Replies(200), engine=engine)

    update, _ = await run(env, state)

    assert not update["verification"].ok
    assert update["verification"].checks[0].detail == "is exited"


async def test_a_container_that_recovers_between_checks_passes(tmp_path):
    engine = FakeEngine("a", [box("shop-web-1", "shop", state="exited")])
    sleeps = Sleeps(
        lambda n: setattr(engine.container_list[0], "state", "running") if n == 2 else None
    )
    env, state = await setup(tmp_path, Replies(200), sleeps=sleeps, engine=engine)

    update, _ = await run(env, state)

    assert update["verification"].ok and update["verification"].attempt == 2


async def test_a_failure_hands_the_failed_checks_to_investigate(tmp_path):
    env, state = await setup(tmp_path, Replies(503))

    update, route = await run(env, state)

    assert route == "investigate"
    assert update["verify_loops"] == 1 and "outcome" not in update
    text = update["messages"][0].content
    assert URL in text and "503" in text and "shop-web-1" not in text  # only what failed


async def test_two_failed_loops_end_unverified_and_never_say_fixed(tmp_path):
    env, state = await setup(tmp_path, Replies(503))
    state["verify_loops"] = 2

    update, route = await run(env, state)

    assert update["outcome"] == "unverified" and route == "report"
    assert "messages" not in update
    assert "fixed" not in update["verification"].detail.lower()
    assert "fixed" not in env.deps.notifier.results[0].detail.lower()


async def test_it_loops_back_at_most_twice(tmp_path):
    env, state = await setup(tmp_path, Replies(503))
    routes = []
    for _ in range(3):
        update, route = await run(env, state)
        state = {**state, **update}
        routes.append(route)
    assert routes == ["investigate", "investigate", "report"]
    assert state["outcome"] == "unverified"


async def test_an_empty_plan_falls_back_to_the_services_containers_and_urls(tmp_path):
    replies = Replies(200)
    env, state = await setup(tmp_path, replies, plan=VerificationPlan())

    update, _ = await run(env, state)

    targets = [(c.kind, c.target) for c in update["verification"].checks]
    assert targets == [("container", "shop-web-1"), ("container", "shop-db-1"), ("url", URL)]
    assert update["outcome"] == "resolved"


async def test_with_nothing_to_check_it_is_unverified_at_once(tmp_path):
    env, state = await setup(tmp_path, Replies(200), plan=VerificationPlan())
    state["subject"] = Subject(kind="host", host_id="a")
    state["proposal"] = state["proposal"].model_copy(update={"steps": []})
    sleeps = env.deps.sleep

    update, route = await run(env, state)

    assert update["outcome"] == "unverified" and route == "report"
    assert update["verification"].detail == "Nothing could be checked."
    assert sleeps.waits == []


async def test_a_plan_url_unknown_to_the_service_is_checked_too(tmp_path):
    replies = Replies(200)
    plan = VerificationPlan(urls=["https://other.example/up"])
    env, state = await setup(tmp_path, replies, plan=plan, urls=False)

    update, _ = await run(env, state)

    assert [(c.kind, c.ok) for c in update["verification"].checks] == [("url", True)]


async def test_a_malformed_plan_url_fails_the_check_instead_of_crashing(tmp_path):
    plan = VerificationPlan(urls=["http://host:abc/"])
    env, state = await setup(tmp_path, Replies(200), plan=plan)

    update, route = await run(env, state)

    assert not update["verification"].ok and route == "investigate"


async def test_a_host_that_is_down_fails_the_container_check(tmp_path):
    engine = healthy_engine()
    env, state = await setup(tmp_path, Replies(200), engine=engine)
    engine.unavailable = True

    update, _ = await run(env, state)

    assert update["verification"].checks[0].detail == "host not reachable"


async def test_each_check_publishes_an_event_and_the_notifier_hears_the_final_result_once(tmp_path):
    env, state = await setup(tmp_path, Replies(503, 200))

    await run(env, state)

    events = [e for e in await env.bus.history(state["incident_id"]) if e.kind == "verification"]
    assert [(e.payload["attempt"], e.payload["ok"]) for e in events] == [(1, False), (2, True)]
    assert [r.attempt for r in env.deps.notifier.results] == [2]


async def test_a_notifier_that_fails_does_not_fail_the_verification(tmp_path):
    env, state = await setup(tmp_path, Replies(200))

    class Broken:
        async def verification(self, incident, result) -> None:
            raise RuntimeError("telegram down")

    env.deps.notifier = Broken()

    update, _ = await run(env, state)

    assert update["outcome"] == "resolved"
