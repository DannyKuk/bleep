"""A look on request in the graph's prompts (#410): a run the person's look alone opened tells
Investigate, Diagnose and Report that nothing had been seen failing; any other run's prompts stay
as they are. A bare look that ends with no action is checked by code, as Verify checks, and ends
`healthy` when every check passes (#421)."""

import httpx
from test_diagnose import reply
from test_diagnose import setup as diagnose_setup
from test_investigate import DONE
from test_investigate import setup as investigate_setup
from test_report import NO_ACTION, Notified, written
from test_report import setup as report_setup

from bleep import prompts
from bleep.graph.look import bare_look, look_rule
from bleep.graph.nodes import diagnose, investigate, report
from bleep.hosts.config import ServiceOverrides, UrlCheckConfig
from bleep.schemas.graph import Decision, GatherContext, PlanStep, Proposal, VerificationPlan
from bleep.schemas.incidents import LOOK_REQUESTED, Signal, Subject
from fakes import FakeChatModel, FakeEngine
from graph_env import T0, box, graph_env, shop_host

SUBJECT = Subject(kind="service", host_id="a", service_id="a/compose:shop")
LOOK = Signal(subject=SUBJECT, kind=LOOK_REQUESTED, at=T0, detail={"via": "dashboard"})
DIED = Signal(subject=SUBJECT, kind="container_died", at=T0, container="shop-web-1")


def test_only_a_run_the_look_alone_opened_is_a_bare_look():
    assert bare_look([LOOK])
    assert not bare_look([LOOK, DIED])  # a failure attached to the look: something broke
    assert not bare_look([DIED])
    assert not bare_look([])


def test_the_rule_is_the_nodes_prompt_file_for_a_bare_look_and_empty_otherwise():
    rule = look_rule("diagnose", [LOOK])
    assert rule == "\n\n" + prompts.load("graph/look_diagnose").strip()
    assert look_rule("diagnose", [LOOK, DIED]) == ""


def test_a_run_that_is_no_look_gets_the_prompt_files_as_they_were():
    text = prompts.file_text("graph/report")
    filled = {name: f"<{name}>" for name in prompts.placeholders(text)} | {"look": ""}
    assert prompts.load("graph/report", **filled) == text.replace("{look}", "").format(**filled)


def look_signals(state: dict) -> dict:
    subject = state["subject"]
    return state | {"signals": [LOOK.model_copy(update={"subject": subject})]}


async def test_investigate_is_told_a_look_names_where_it_was_asked_not_what_failed(tmp_path):
    env, state = await investigate_setup(tmp_path, [DONE])
    await investigate.make(env.deps)(look_signals(state))
    task = str(env.models.model.seen[0][1].content)
    assert "asked you to look at this service now" in task
    assert "not a part of the service" in task


async def test_investigate_of_a_failure_is_not_told_about_looks(tmp_path):
    env, state = await investigate_setup(tmp_path, [DONE])
    await investigate.make(env.deps)(state)
    assert "look_requested" not in str(env.models.model.seen[0][1].content)


async def test_diagnose_of_a_look_may_say_nothing_is_wrong(tmp_path):
    env, state = await diagnose_setup(tmp_path, [reply(0.9)])
    await diagnose.make(env.deps)(look_signals(state))
    sent = "\n".join(str(m.content) for m in env.models.model.seen[0])
    assert "says that nothing is wrong" in sent and "how sure you are that it is healthy" in sent


async def test_diagnose_of_a_failure_is_not_told_about_looks(tmp_path):
    env, state = await diagnose_setup(tmp_path, [reply(0.9)])
    await diagnose.make(env.deps)(state)
    assert "look_requested" not in "\n".join(str(m.content) for m in env.models.model.seen[0])


async def test_the_report_of_a_look_says_a_healthy_service_was_no_issue(tmp_path):
    env, state, _ = await report_setup(tmp_path, written())
    await report.make(env.deps)(look_signals(state) | {"proposal": NO_ACTION})
    sent = "\n".join(m.text for m in env.models.model.seen[0])
    assert "nothing had been seen failing" in sent and "Do not call it an issue" in sent


async def test_the_report_of_a_failure_is_not_told_about_looks(tmp_path):
    env, state, _ = await report_setup(tmp_path, written())
    await report.make(env.deps)(state | {"proposal": NO_ACTION})
    assert "nothing had been seen failing" not in "\n".join(
        m.text for m in env.models.model.seen[0]
    )


# --- a look that finds the service healthy (#421) ---

URL = "https://shop.example/health"
HEALTHY_LOOK = Proposal(kind="no_action", reason="Nothing is wrong with shop.")


def answering(status: int):
    def reply(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    return reply


def healthy_engine() -> FakeEngine:
    return FakeEngine("a", [box("shop-web-1", "shop", health="healthy"), box("shop-db-1", "shop")])


async def look_setup(tmp_path, status: int = 200, engine: FakeEngine | None = None):
    host = shop_host(services={"shop": ServiceOverrides(urls=[UrlCheckConfig(url=URL)])})
    env = await graph_env(tmp_path, engine=engine or healthy_engine(), host=host)
    env.models.model = FakeChatModel(script=[written()] * 2, structured_output_unsupported=True)
    env.deps.http = httpx.AsyncClient(transport=httpx.MockTransport(answering(status)))
    notified = Notified()
    env.deps.notifier = notified  # type: ignore[assignment]
    subject = Subject(kind="service", host_id="a", service_id=env.service_id("shop"))
    signal = LOOK.model_copy(update={"subject": subject})
    incident, _ = await env.repo.open_or_attach(subject, [signal], name="shop", attach=False)
    state = {
        "incident_id": incident.id,
        "subject": subject,
        "signals": [signal],
        "context": GatherContext(at=T0),
        "proposal": HEALTHY_LOOK,
    }
    return env, state, notified


async def verifications(env, incident_id: str) -> list[dict]:
    return [e.payload for e in await env.bus.history(incident_id) if e.kind == "verification"]


async def test_a_look_whose_checks_pass_ends_healthy(tmp_path):
    env, state, notified = await look_setup(tmp_path)

    update = await report.make(env.deps)(state)

    assert update["outcome"] == "healthy"
    assert update["report"].outcome == report.OUTCOME_TEXT["healthy"]
    assert notified.reports[0][1].outcome == report.OUTCOME_TEXT["healthy"]
    [checked] = await verifications(env, state["incident_id"])
    assert checked["ok"] is True
    targets = {c["target"]: c["ok"] for c in checked["checks"]}
    assert targets == {"shop-web-1": True, "shop-db-1": True, URL: True}
    assert any(e.text == "Check 1 passed" for e in update["report"].timeline)
    assert env.models.calls == ["main"]  # the report's own call: the check asks no model


async def test_a_look_checks_what_its_plan_names_and_the_services_urls(tmp_path):
    env, state, _ = await look_setup(tmp_path)
    plan = HEALTHY_LOOK.model_copy(
        update={"verification": VerificationPlan(containers=["shop-db-1"])}
    )

    update = await report.make(env.deps)(state | {"proposal": plan})

    assert update["outcome"] == "healthy"
    [checked] = await verifications(env, state["incident_id"])
    assert {c["target"] for c in checked["checks"]} == {"shop-db-1", URL}


async def test_a_look_whose_url_fails_ends_as_no_action(tmp_path):
    env, state, _ = await look_setup(tmp_path, status=503)

    update = await report.make(env.deps)(state)

    assert update["outcome"] == "no_action"
    assert update["report"].outcome == report.OUTCOME_TEXT["no_action"]
    [checked] = await verifications(env, state["incident_id"])
    assert checked["ok"] is False
    assert any(e.text == "Check 1 failed" for e in update["report"].timeline)


async def test_a_look_at_a_container_that_is_down_ends_as_no_action(tmp_path):
    engine = FakeEngine(
        "a", [box("shop-web-1", "shop", state="exited", exit_code=1), box("shop-db-1", "shop")]
    )
    env, state, _ = await look_setup(tmp_path, engine=engine)

    update = await report.make(env.deps)(state)

    assert update["outcome"] == "no_action"


async def test_a_look_at_an_unhealthy_container_ends_as_no_action(tmp_path):
    engine = FakeEngine("a", [box("shop-web-1", "shop", health="unhealthy")])
    env, state, _ = await look_setup(tmp_path, engine=engine)

    update = await report.make(env.deps)(state)

    assert update["outcome"] == "no_action"


async def test_a_look_whose_host_stops_answering_during_the_check_ends_as_no_action(tmp_path):
    env, state, _ = await look_setup(tmp_path)
    env.engine.unavailable = True

    update = await report.make(env.deps)(state)

    assert update["outcome"] == "no_action"
    [checked] = await verifications(env, state["incident_id"])
    details = {c["detail"] for c in checked["checks"] if c["kind"] == "container"}
    assert details == {"host not reachable"}


async def test_a_look_whose_plan_was_refused_and_let_go_is_not_checked(tmp_path):
    env, state, _ = await look_setup(tmp_path)
    refused = HEALTHY_LOOK.model_copy(update={"refused": True})
    let_go = Decision(kind="reject", via="telegram", at=T0)

    update = await report.make(env.deps)(state | {"proposal": refused, "decision": let_go})

    assert update["outcome"] == "no_action"
    assert await verifications(env, state["incident_id"]) == []


async def test_a_look_whose_plan_was_rejected_is_never_healthy(tmp_path):
    env, state, _ = await look_setup(tmp_path)
    act = Proposal(
        kind="act",
        steps=[PlanStep(action="docker.restart", host_id="a", target="shop-web-1")],
        reason="r",
    )
    rejected = Decision(kind="reject", via="dashboard", at=T0)

    update = await report.make(env.deps)(state | {"proposal": act, "decision": rejected})

    assert update["outcome"] == "rejected"
    assert await verifications(env, state["incident_id"]) == []


async def test_a_failure_that_ends_with_no_action_is_not_checked(tmp_path):
    env, state, _ = await look_setup(tmp_path)
    died = DIED.model_copy(update={"subject": state["subject"]})

    for signals in ([died], [state["signals"][0], died]):
        update = await report.make(env.deps)(state | {"signals": signals})
        assert update["outcome"] == "no_action"
    assert await verifications(env, state["incident_id"]) == []
