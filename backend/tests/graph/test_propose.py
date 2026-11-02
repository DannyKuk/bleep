from typing import Any

from langchain_core.messages import AIMessage

from bleep.graph.deps import StaticSettings
from bleep.graph.nodes import gather, propose
from bleep.guard.plan_check import check_plan
from bleep.hosts.config import ServiceOverrides, UrlCheckConfig
from bleep.schemas.graph import Diagnosis, Evidence, PlanStep, Proposal
from bleep.schemas.incidents import Signal, Subject
from bleep.schemas.settings import Budgets
from fakes import FakeEngine
from graph_env import T0, box, graph_env, shop_host

HEALTH = "http://localhost:8080/health"
CRASH_LOG = (
    "INFO starting shop-web\n"
    'sqlalchemy.exc.OperationalError: could not translate host name "wrong-db" to address\n'
)
# What the human must do on plain Compose: Bleep cannot change an env var or recreate a container.
FIX_ENV = (
    "In the shop Compose file, set DATABASE_URL of shop-web back to the shop-db host "
    "(postgresql://shop@shop-db:5432/shop instead of wrong-db), then run "
    "`docker compose up -d shop-web` to recreate it."
)
WEB_BROKEN = Diagnosis(
    root_cause="shop-web's DATABASE_URL names the host wrong-db, which does not exist",
    category="config",
    evidence=[Evidence(source="gather", detail='could not translate host name "wrong-db"')],
    confidence=0.9,
    affected_services=["shop"],
)


def bad_env_engine() -> FakeEngine:
    """The demo stack's bad-env fault: shop-web crash-loops on a DATABASE_URL naming no host."""
    return FakeEngine(
        "a",
        [
            box(
                "shop-web-1",
                "shop",
                state="restarting",
                exit_code=1,
                restart_count=7,
                restart_policy="unless-stopped",
            ),
            box("shop-db-1", "shop"),
            box("shop-proxy-1", "shop"),
            box("bleep-backend-1", "bleep", labels={"bleep.self": "true"}),
        ],
        logs={"shop-web-1": CRASH_LOG},
    )


def demo_host(protected: list[str] | None = None, key: str = "shop"):
    shop = ServiceOverrides(urls=[UrlCheckConfig(url=HEALTH)], protected_containers=protected or [])
    return shop_host(rules=[], services={key: shop})


def reply(**proposal: Any) -> AIMessage:
    """The model's answer as a structured-output tool call."""
    return AIMessage(content="", tool_calls=[{"name": "Proposal", "args": proposal, "id": "p"}])


def restart(target: str) -> dict:
    return {"action": "docker.restart", "host_id": "a", "target": target}


def acting(*steps: dict) -> AIMessage:
    return reply(kind="act", steps=list(steps), reason="restart it", verification={})


async def setup(
    tmp_path,
    *script: AIMessage,
    protected: list[str] | None = None,
    key: str = "shop",
    **state: Any,
):
    """A graph env on the bad-env stack, the model scripted, and Propose's input state."""
    env = await graph_env(tmp_path, engine=bad_env_engine(), host=demo_host(protected, key))
    env.models.model.script = list(script)
    env.models.model.structured_output_unsupported = False  # the replies are tool calls
    subject = Subject(kind="service", host_id="a", service_id=env.service_id("shop"))
    signals = [Signal(subject=subject, kind="restart_loop", at=T0, container="shop-web-1")]
    incident = await env.open(subject, kind="restart_loop", container="shop-web-1")
    base = {"incident_id": incident.id, "subject": subject, "signals": signals}
    context = (await gather.make(env.deps)(base))["context"]
    return env, base | {"context": context, "diagnosis": WEB_BROKEN} | state


def blocked(env) -> list[str]:
    return [detail for _, kind, detail in env.guard_log.records if kind == "blocked_action"]


def prompt(env, call: int = 0) -> str:
    return "\n".join(m.text for m in env.models.model.seen[call])


async def test_bad_env_on_plain_compose_gives_no_action_with_exact_instructions(tmp_path):
    answer = reply(
        kind="no_action",
        steps=[],
        reason="Bleep cannot change an environment variable on a plain Compose stack.",
        verification={"containers": ["shop-web-1"], "urls": [HEALTH]},
        human_instructions=FIX_ENV,
    )
    env, state = await setup(tmp_path, answer)

    update = await propose.make(env.deps)(state)

    proposal = update["proposal"]
    assert proposal.kind == "no_action" and proposal.steps == []
    assert proposal.human_instructions == FIX_ENV
    assert proposal.verification.urls == [HEALTH]
    assert check_plan(proposal, env.registry, env.deps.settings_store.enabled_actions(), "a") == []
    assert env.models.model.cursor == 1  # passed the check first time: no retry
    assert blocked(env) == []
    sent = prompt(env)
    assert "wrong-db" in sent  # the diagnosis
    assert "shop-web-1: restarting" in sent  # the containers
    assert "bleep-backend-1" in sent.split("## Rules")[0].split("## Off limits")[1]
    assert HEALTH in sent
    events = [e for e in await env.bus.history(state["incident_id"]) if e.kind == "proposal"]
    assert events[-1].payload["human_instructions"] == FIX_ENV


async def test_a_protected_container_is_refused_in_code_and_logged(tmp_path):
    # The model insists on restarting the protected database: refused twice, it degrades.
    env, state = await setup(
        tmp_path,
        acting(restart("shop-db-1")),
        acting(restart("shop-db-1")),
        protected=["shop-db-1"],
    )

    proposal = (await propose.make(env.deps)(state))["proposal"]

    assert proposal.kind == "no_action" and proposal.steps == []
    assert "shop-db-1 is protected" in proposal.human_instructions
    assert "docker.restart shop-db-1" in proposal.human_instructions  # what was refused
    assert len(blocked(env)) == 2 and all("shop-db-1" in d for d in blocked(env))
    retry = env.models.model.seen[1][-1].text
    assert "shop-db-1 is protected" in retry  # the violation went back to the model once
    assert env.models.model.cursor == 2


async def test_a_plan_targeting_bleeps_own_backend_is_refused(tmp_path):
    # Review Focus 4: even when the model names Bleep's container, the plan never reaches a human.
    env, state = await setup(
        tmp_path, acting(restart("bleep-backend-1")), acting(restart("shop-web-1"))
    )

    proposal = (await propose.make(env.deps)(state))["proposal"]

    assert proposal.kind == "act"
    assert proposal.steps == [PlanStep(action="docker.restart", host_id="a", target="shop-web-1")]
    assert len(blocked(env)) == 1
    assert "bleep-backend-1 is one of Bleep's own containers" in blocked(env)[0]


async def test_coolify_redeploy_is_refused_while_the_plugin_is_disabled(tmp_path):
    redeploy = {"action": "coolify.redeploy", "host_id": "a", "target": "shop"}
    env, state = await setup(tmp_path, acting(redeploy), acting(redeploy))

    proposal = (await propose.make(env.deps)(state))["proposal"]

    assert proposal.kind == "no_action"
    assert "not enabled" in proposal.human_instructions
    assert len(blocked(env)) == 2
    assert "coolify.redeploy" not in prompt(env).split("Enabled now")[1].split("\n")[0]


async def test_the_humans_instructions_are_in_the_next_prompt(tmp_path):
    before = Proposal(
        kind="act",
        steps=[PlanStep(action="docker.restart", host_id="a", target="shop-web-1")],
        reason="restart it",
    )
    answer = reply(kind="no_action", steps=[], reason="no redeploy here", human_instructions="x")
    env, state = await setup(tmp_path, answer, proposal=before, instructions=["redeploy instead"])

    await propose.make(env.deps)(state)

    sent = prompt(env)
    assert "redeploy instead" in sent.split("## What the human told you")[1]
    assert "docker.restart shop-web-1" in sent.split("## Your previous plan")[1]


async def test_an_unsure_diagnosis_proposes_no_action_without_a_model(tmp_path):
    unsure = WEB_BROKEN.model_copy(update={"unsure": True, "confidence": 0.3})
    env, state = await setup(tmp_path, diagnosis=unsure)

    proposal = (await propose.make(env.deps)(state))["proposal"]

    assert proposal.kind == "no_action" and proposal.steps == []
    assert env.models.calls == []


async def test_a_model_that_returns_no_usable_plan_degrades_to_no_action(tmp_path):
    env, state = await setup(tmp_path, AIMessage("no idea"), AIMessage("no idea"), AIMessage("?"))
    env.models.model.structured_output_unsupported = True

    proposal = (await propose.make(env.deps)(state))["proposal"]

    assert proposal.kind == "no_action" and proposal.steps == []
    assert blocked(env) == []


async def test_a_spent_budget_stops_before_the_model_is_called(tmp_path):
    env, state = await setup(tmp_path)
    env.deps.settings_store = StaticSettings(budget_limits=Budgets(daily_cost_usd=0))

    update = await propose.make(env.deps)(state)

    assert update["outcome"] == "budget"
    assert update["proposal"].kind == "no_action"
    assert env.models.model.seen == []


def test_route_sends_only_an_act_plan_to_approval():
    act = Proposal(kind="act", steps=[], reason="x")
    assert propose.route({"proposal": act}) == "approval"
    assert propose.route({"proposal": Proposal(kind="no_action", reason="x")}) == "report"
    assert propose.route({}) == "report"


async def test_off_limits_lists_protected_containers_of_an_override_matching_no_service(tmp_path):
    env, state = await setup(
        tmp_path, acting(restart("shop-web-1")), protected=["shop-db-1"], key="database"
    )

    await propose.make(env.deps)(state)

    assert "- shop-db-1 (protected)" in prompt(env)
