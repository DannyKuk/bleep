"""A look on request that ends with no action is checked by code and ends `healthy` when every
check of its service passes (#421): the service's own containers against Gather's reading, its
URLs from the hosts file, and nothing a model named that is not the service's. Every other run
ends as before."""

import logging

import httpx
from test_report import Notified, written

from bleep.graph.nodes import gather, report
from bleep.graph.nodes.diagnose import FALLBACK_CAUSE
from bleep.graph.nodes.propose import NO_PLAN
from bleep.hosts.config import ServiceOverrides, UrlCheckConfig
from bleep.hosts.engine import EngineError
from bleep.schemas.graph import Decision, Diagnosis, PlanStep, Proposal, VerificationPlan
from bleep.schemas.incidents import LOOK_REQUESTED, Signal, Subject
from fakes import FakeChatModel, FakeEngine
from graph_env import T0, box, graph_env, shop_host

URL = "https://shop.example/health"
PLANTED = "http://169.254.169.254/latest/meta-data/"
HEALTHY = Diagnosis(root_cause="shop is healthy: both run", category="unknown", confidence=0.9)
LOOKED = Proposal(kind="no_action", reason="Nothing is wrong with shop.")


class Replies:
    """The URLs Bleep requested, each answered with `status`."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.seen: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(str(request.url))
        return httpx.Response(self.status)


def healthy_engine() -> FakeEngine:
    return FakeEngine(
        "a",
        [
            box("shop-web-1", "shop", health="healthy"),
            box("shop-db-1", "shop"),
            box("blog-1", "blog"),
        ],
    )


async def setup(tmp_path, status: int = 200, engine: FakeEngine | None = None, urls=(URL,)):
    """A look at shop through Gather, as the run reads it, with a plan of no action."""
    overrides = ServiceOverrides(urls=[UrlCheckConfig(url=u) for u in urls])
    host = shop_host(services={"shop": overrides})
    env = await graph_env(tmp_path, engine=engine or healthy_engine(), host=host)
    env.models.model = FakeChatModel(script=[written()] * 2, structured_output_unsupported=True)
    replies = Replies(status)
    env.deps.http = httpx.AsyncClient(transport=httpx.MockTransport(replies))
    env.deps.notifier = Notified()  # type: ignore[assignment]
    subject = Subject(kind="service", host_id="a", service_id=env.service_id("shop"))
    look = Signal(subject=subject, kind=LOOK_REQUESTED, at=T0, detail={"via": "telegram"})
    incident, _ = await env.repo.open_or_attach(subject, [look], name="shop", attach=False)
    base = {"incident_id": incident.id, "subject": subject, "signals": [look]}
    context = (await gather.make(env.deps)(base))["context"]
    state = base | {"context": context, "diagnosis": HEALTHY, "proposal": LOOKED}
    return env, state, replies


async def run(env, state) -> dict:
    return await report.make(env.deps)(state)


def planning(**verification) -> dict:
    plan = VerificationPlan(**verification)
    return {"proposal": LOOKED.model_copy(update={"verification": plan})}


async def checks(env, state) -> list[dict]:
    """The checks of the one `verification` event the run published; [] when it published none."""
    events = [e for e in await env.bus.history(state["incident_id"]) if e.kind == "verification"]
    assert len(events) <= 1
    return events[0].payload["checks"] if events else []


def set_container(engine: FakeEngine, name: str, **fields) -> None:
    index = next(i for i, c in enumerate(engine.container_list) if c.name == name)
    engine.container_list[index] = engine.container_list[index].model_copy(update=fields)


# --- healthy ---


async def test_a_look_whose_checks_pass_ends_healthy(tmp_path):
    env, state, replies = await setup(tmp_path)

    update = await run(env, state)

    assert update["outcome"] == "healthy"
    assert update["report"].outcome == report.OUTCOME_TEXT["healthy"]
    found = {c["target"]: c["ok"] for c in await checks(env, state)}
    assert found == {"shop-web-1": True, "shop-db-1": True, URL: True}
    assert replies.seen == [URL]
    assert any(e.text == "Check 1 passed" for e in update["report"].timeline)
    assert env.models.calls == ["main"]  # the report's own call: the check asks no model


async def test_a_healthy_looks_advice_is_not_made_a_follow_up(tmp_path):
    env, state, _ = await setup(tmp_path)
    env.models.model = FakeChatModel(
        script=[written(follow_ups=[], summary="shop was healthy.")] * 2,
        structured_output_unsupported=True,
    )
    advice = LOOKED.model_copy(update={"human_instructions": "Set SMTP_HOST in .env."})

    update = await run(env, state | {"proposal": advice})

    assert update["outcome"] == "healthy"
    assert update["report"].follow_ups == []


async def test_a_job_that_finished_does_not_keep_a_look_from_being_healthy(tmp_path):
    engine = healthy_engine()
    engine.container_list.append(
        box("shop-migrate-1", "shop", state="exited", exit_code=0, restart_policy="no")
    )
    env, state, _ = await setup(tmp_path, engine=engine)

    update = await run(env, state)

    assert update["outcome"] == "healthy"
    assert "shop-migrate-1" not in {c["target"] for c in await checks(env, state)}


# --- what is checked: the service, never what a model names instead ---


async def test_a_model_url_is_never_requested(tmp_path):
    env, state, replies = await setup(tmp_path)

    update = await run(env, state | planning(urls=[PLANTED, "https://example.com/"]))

    assert replies.seen == [URL]
    assert {c["target"] for c in await checks(env, state)} == {"shop-web-1", "shop-db-1", URL}
    assert update["outcome"] == "healthy"  # on the service's own checks


async def test_a_model_url_that_answers_never_makes_a_look_healthy(tmp_path):
    engine = healthy_engine()
    set_container(engine, "shop-web-1", state="exited", exit_code=1)
    env, state, replies = await setup(tmp_path, engine=engine, urls=())

    update = await run(env, state | planning(urls=["https://example.com/"]))

    assert replies.seen == []
    assert update["outcome"] == "no_action"


async def test_a_plan_of_another_services_container_does_not_replace_the_services_own(tmp_path):
    engine = healthy_engine()
    set_container(engine, "shop-web-1", state="exited", exit_code=1)
    env, state, _ = await setup(tmp_path, engine=engine, urls=())

    update = await run(env, state | planning(containers=["blog-1"]))

    assert update["outcome"] == "no_action"
    found = {c["target"]: c["ok"] for c in await checks(env, state)}
    assert found == {"shop-web-1": False, "shop-db-1": True}  # blog-1 is not shop's


async def test_a_plan_of_one_of_the_services_containers_still_checks_them_all(tmp_path):
    env, state, _ = await setup(tmp_path)

    update = await run(env, state | planning(containers=["shop-db-1"]))

    assert update["outcome"] == "healthy"
    assert {c["target"] for c in await checks(env, state)} == {"shop-web-1", "shop-db-1", URL}


# --- a check that fails keeps no_action ---


async def test_a_look_whose_url_fails_ends_as_no_action(tmp_path):
    env, state, _ = await setup(tmp_path, status=503)

    update = await run(env, state)

    assert update["outcome"] == "no_action"
    assert update["report"].outcome == report.OUTCOME_TEXT["no_action"]
    assert [c["target"] for c in await checks(env, state) if not c["ok"]] == [URL]
    assert any(e.text == "Check 1 failed" for e in update["report"].timeline)


async def test_a_look_at_a_container_that_is_down_or_unhealthy_ends_as_no_action(tmp_path):
    for fields in ({"state": "exited", "exit_code": 1}, {"health": "unhealthy"}):
        engine = healthy_engine()
        set_container(engine, "shop-web-1", **fields)
        env, state, _ = await setup(tmp_path / str(len(fields)), engine=engine)

        assert (await run(env, state))["outcome"] == "no_action"


async def test_a_crash_looper_caught_while_it_is_up_ends_as_no_action(tmp_path):
    engine = healthy_engine()
    set_container(engine, "shop-web-1", restart_count=3)
    env, state, _ = await setup(tmp_path, engine=engine)
    set_container(engine, "shop-web-1", restart_count=4)  # its restart policy, since Gather

    update = await run(env, state)

    assert update["outcome"] == "no_action"
    [web] = [c for c in await checks(env, state) if c["target"] == "shop-web-1"]
    assert not web["ok"] and "restart count 4, 3 when Verify first saw it running" in web["detail"]


async def test_a_container_that_came_up_since_gather_ends_as_no_action(tmp_path):
    engine = healthy_engine()
    set_container(engine, "shop-web-1", state="restarting")
    env, state, _ = await setup(tmp_path, engine=engine)
    set_container(engine, "shop-web-1", state="running")

    update = await run(env, state)

    assert update["outcome"] == "no_action"
    [web] = [c for c in await checks(env, state) if c["target"] == "shop-web-1"]
    assert web["detail"] == "not running when Bleep gathered the context"  # no "next check"


async def test_a_look_whose_host_stops_answering_during_the_check_ends_as_no_action(tmp_path):
    env, state, _ = await setup(tmp_path)
    env.engine.unavailable = True

    update = await run(env, state)

    assert update["outcome"] == "no_action"
    containers = [c for c in await checks(env, state) if c["kind"] == "container"]
    assert {c["detail"] for c in containers} == {"host not reachable"}


async def test_an_engine_error_during_the_check_ends_as_no_action_and_is_logged(tmp_path, caplog):
    env, state, _ = await setup(tmp_path)

    async def refused(name: str):
        raise EngineError("a", "engine refused: 500")

    env.engine.inspect = refused  # type: ignore[method-assign]

    with caplog.at_level(logging.WARNING, logger="bleep.graph.nodes.report"):
        update = await run(env, state)

    assert update["outcome"] == "no_action"
    assert "checking the look" in caplog.text and "EngineError" in caplog.text


# --- runs that are never checked ---


async def test_a_watcher_signal_attached_during_the_run_keeps_no_action(tmp_path):
    env, state, _ = await setup(tmp_path)
    subject = state["subject"]
    died = Signal(subject=subject, kind="unhealthy", at=T0, container="shop-web-1")
    await env.repo.open_or_attach(subject, [died])  # attaches to the open look

    update = await run(env, state)

    assert update["outcome"] == "no_action"
    assert await checks(env, state) == []


async def test_a_look_the_model_did_not_find_healthy_is_not_checked(tmp_path):
    env, state, _ = await setup(tmp_path)
    config = HEALTHY.model_copy(update={"category": "config", "root_cause": "SMTP_HOST unset"})
    fallback = HEALTHY.model_copy(update={"root_cause": FALLBACK_CAUSE})

    for changed in (
        {"diagnosis": config},
        {"diagnosis": fallback},
        {"diagnosis": None},
        {"proposal": LOOKED.model_copy(update={"reason": NO_PLAN})},
        {"replans": 1},
    ):
        update = await run(env, state | changed)
        assert update["outcome"] == "no_action", changed
    assert await checks(env, state) == []


async def test_an_unsure_diagnosis_keeps_no_action_and_its_advice(tmp_path):
    env, state, _ = await setup(tmp_path)
    env.models.model = FakeChatModel(
        script=[written(follow_ups=[], summary="shop was healthy.")] * 2,
        structured_output_unsupported=True,
    )
    unsure = Diagnosis(
        root_cause="The worker logs repeated job failures; the cause is unclear.",
        category="unknown",
        confidence=0.4,
        unsure=True,
    )
    advice = "Check the worker's SMTP settings."
    plan = LOOKED.model_copy(update={"reason": "No allowed action fixes this."})
    plan = plan.model_copy(update={"human_instructions": advice})

    update = await run(env, state | {"diagnosis": unsure, "proposal": plan})

    assert update["outcome"] == "no_action"
    assert update["report"].follow_ups == [advice]
    assert await checks(env, state) == []


async def test_a_look_whose_plan_was_refused_and_let_go_is_not_checked(tmp_path):
    env, state, _ = await setup(tmp_path)
    refused = LOOKED.model_copy(update={"refused": True})
    let_go = Decision(kind="reject", via="telegram", at=T0)

    update = await run(env, state | {"proposal": refused, "decision": let_go})

    assert update["outcome"] == "no_action"
    assert await checks(env, state) == []


async def test_a_look_whose_plan_was_rejected_is_never_healthy(tmp_path):
    env, state, _ = await setup(tmp_path)
    act = Proposal(
        kind="act",
        steps=[PlanStep(action="docker.restart", host_id="a", target="shop-web-1")],
        reason="r",
    )
    rejected = Decision(kind="reject", via="dashboard", at=T0)

    update = await run(env, state | {"proposal": act, "decision": rejected})

    assert update["outcome"] == "rejected"
    assert await checks(env, state) == []


async def test_a_failure_that_ends_with_no_action_is_not_checked(tmp_path):
    env, state, _ = await setup(tmp_path)
    look = state["signals"][0]
    died = Signal(subject=state["subject"], kind="container_died", at=T0, container="shop-web-1")

    for signals in ([died], [look, died]):
        update = await run(env, state | {"signals": signals})
        assert update["outcome"] == "no_action"
    assert await checks(env, state) == []
