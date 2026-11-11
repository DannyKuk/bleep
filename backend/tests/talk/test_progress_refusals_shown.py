"""The chat panel's summary of a waiting plan gives Bleep's texts in place of the model's in the
picked language (#455)."""

from test_progress import PLAN, env, look  # noqa: F401  (the fixture is the progress tests')

from bleep import prompts
from bleep.graph.nodes import diagnose, propose
from bleep.schemas.graph import Diagnosis


def de(name: str, **values: str) -> str:
    return prompts.load(f"talk/de/{name}", **values).strip()


async def test_a_waiting_plan_names_the_fallback_cause_in_german(env):  # noqa: F811
    env.language = "de"
    await env.approvals.request(env.incident.id, PLAN, [])
    await env.repo.set_status(env.incident.id, "awaiting_approval")
    env.runs.values = {
        "diagnosis": Diagnosis(
            root_cause=diagnose.FALLBACK_CAUSE,
            category="unknown",
            confidence=0.0,
            shown=diagnose.FALLBACK_SHOWN,
        )
    }

    waiting = await look(env)

    cause = "Ich konnte keine Diagnose stellen"
    assert waiting.line == de("look_end/waits", incident="shop #1", cause=cause)


async def test_a_refused_plan_without_a_diagnosis_gives_bleeps_reason_in_german(env):  # noqa: F811
    env.language = "de"
    refused = PLAN.model_copy(
        update={
            "refused": True,
            "reason": propose.REFUSED,
            "shown": {"reason": propose.SHOWN[propose.REFUSED]},
        }
    )
    await env.approvals.request(env.incident.id, refused, [])
    await env.repo.set_status(env.incident.id, "awaiting_approval")

    waiting = await look(env)

    cause = (
        "Bleeps eigene Prüfungen haben den vorgeschlagenen Plan zweimal zurückgewiesen, also läuft "
        "nichts."
    )
    assert waiting.line == de("look_end/waits_refused", incident="shop #1", cause=cause)
