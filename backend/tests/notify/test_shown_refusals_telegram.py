"""The plan check's refusals and Bleep's texts in place of the model's inside Telegram messages, in
the picked language (#455)."""

from test_threads import env  # noqa: F401  (the fixture is the thread tests')

from bleep.graph.nodes import diagnose, propose
from bleep.schemas.graph import Diagnosis, PlanStep, Proposal
from bleep.schemas.shown import Shown

STEP = PlanStep(action="docker.restart", host_id="laptop", target="shop-web")
REASON = Shown.of(
    "refusal/step",
    number="1",
    action="docker.restart",
    target="shop-web",
    host="laptop",
    why=Shown.of("refusal/own_container", name="shop-web"),
)
REFUSED = Proposal(
    kind="no_action",
    reason=propose.REFUSED,
    refused=True,
    refused_steps=[STEP],
    refusal_reasons=["step 1 (docker.restart shop-web on laptop): shop-web is one of Bleep's own "
                     "containers"],
    shown={"reason": propose.SHOWN[propose.REFUSED]},
    refusal_reasons_shown=[REASON],
)  # fmt: skip
FALLBACK = Diagnosis(
    root_cause=diagnose.FALLBACK_CAUSE,
    category="unknown",
    confidence=0.0,
    unsure=True,
    shown=diagnose.FALLBACK_SHOWN,
)


async def ask(env, plan: Proposal, diagnosis: Diagnosis | None = FALLBACK):  # noqa: F811
    incident = await env.open()
    request = await env.approvals.request(incident.id, plan, [])
    await env.notifier.proposal(incident, diagnosis, plan, request)
    return env.chat.sent()[-1]["text"].split("\n")


async def test_a_refused_plan_gives_its_reasons_and_the_fallback_cause_in_german(env):  # noqa: F811
    env.notifier.language = lambda: "de"

    lines = await ask(env, REFUSED)

    assert "Wahrscheinliche Ursache: Ich konnte keine Diagnose stellen" in lines
    assert (
        "\\- Schritt 1 \\(docker\\.restart shop\\-web auf laptop\\): shop\\-web ist einer von "
        "Bleeps eigenen Containern"
    ) in lines


async def test_in_english_a_refused_plan_reads_as_before(env):  # noqa: F811
    lines = await ask(env, REFUSED)

    assert "Likely cause: I could not form a diagnosis" in lines
    assert (
        "\\- step 1 \\(docker\\.restart shop\\-web on laptop\\): shop\\-web is one of Bleep's own "
        "containers"
    ) in lines


async def test_a_plan_the_model_wrote_keeps_its_words_and_bleeps_cause_reads_in_german(env):  # noqa: F811
    env.notifier.language = lambda: "de"
    plan = Proposal(kind="act", steps=[STEP], reason="shop-web needs a restart.")

    lines = await ask(env, plan)

    assert any(line.endswith("Ich konnte keine Diagnose stellen") for line in lines)
    assert "Warum: shop\\-web needs a restart\\." in lines
