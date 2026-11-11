"""The plan card, the diagnosis card and the recoveries list show the plan check's refusals and
Bleep's texts in place of the model's in the picked language; what is stored stays English
(#455)."""

from test_incidents import T0, app_client, call, clock, german, open_incident  # noqa: F401

from bleep.graph.nodes import diagnose, propose
from bleep.schemas.graph import Diagnosis, PlanStep, Proposal
from bleep.schemas.incidents import RecoveryEvent
from bleep.schemas.shown import Shown
from bleep.watcher import manager

REASON = Shown.of(
    "refusal/step",
    number="1",
    action="docker.restart",
    target="shop-web-1",
    host="a",
    why=Shown.of("refusal/protected", name="shop-web-1"),
)
REFUSED = Proposal(
    kind="no_action",
    reason=propose.REFUSED,
    refused=True,
    refused_steps=[PlanStep(action="docker.restart", host_id="a", target="shop-web-1")],
    refusal_reasons=["step 1 (docker.restart shop-web-1 on a): shop-web-1 is protected in the "
                     "hosts file"],
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
REFUSED_DE = (
    "Bleeps eigene Prüfungen haben den vorgeschlagenen Plan zweimal zurückgewiesen, also läuft "
    "nichts."
)


def test_the_plan_and_the_diagnosis_cards_read_in_the_picked_language(
    app_client,  # noqa: F811
    parts,
    monkeypatch,
):
    incident = open_incident(app_client, parts)
    call(app_client, parts.approvals.request, incident.id, REFUSED, [])

    async def fake_state(incident_id: str) -> dict:
        return {"diagnosis": FALLBACK, "proposal": REFUSED}

    monkeypatch.setattr(app_client.app.state.runner, "state", fake_state)
    english = app_client.get(f"/api/incidents/{incident.id}").json()
    german(parts, monkeypatch)
    body = app_client.get(f"/api/incidents/{incident.id}").json()

    assert body["diagnosis"]["root_cause"] == "Ich konnte keine Diagnose stellen"
    for plan in (body["proposal"], body["approval"]["proposal"]):
        assert plan["reason"] == REFUSED_DE
        assert plan["refusal_reasons"] == [
            "Schritt 1 (docker.restart shop-web-1 auf a): shop-web-1 ist in der Hosts-Datei "
            "geschützt"
        ]
    assert english["diagnosis"]["root_cause"] == diagnose.FALLBACK_CAUSE
    assert english["proposal"]["reason"] == propose.REFUSED
    assert english["approval"]["proposal"]["refusal_reasons"] == REFUSED.refusal_reasons
    kept = call(app_client, parts.approvals.current, incident.id)
    assert kept.proposal == REFUSED  # stored as it was, its texts in English


def test_the_recoveries_read_in_the_picked_language(app_client, parts, monkeypatch):  # noqa: F811
    held = RecoveryEvent(
        host_id="a",
        container="shop-web-1",
        at=T0,
        explanation=manager.HELD_LOOP_EXPLANATION,
        explanation_shown=manager.HELD_LOOP_SHOWN,
    )
    call(app_client, parts.incidents.log_recovery, held)
    model = held.model_copy(
        update={"explanation": "It ran out of memory.", "explanation_shown": None}
    )
    call(app_client, parts.incidents.log_recovery, model)

    english = app_client.get("/api/recoveries").json()
    german(parts, monkeypatch)
    body = app_client.get("/api/recoveries").json()

    assert [r["explanation"] for r in english] == [
        "It ran out of memory.",
        manager.HELD_LOOP_EXPLANATION,
    ]
    assert [r["explanation"] for r in body] == [
        "It ran out of memory.",
        "Der Container ist in seiner Absturzschleife wieder ausgefallen, und seine Neustart-Regel "
        "hat ihn zurückgeholt.",
    ]
