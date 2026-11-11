"""The plan check's refusal reasons and the texts Bleep writes in place of the model's, in the
picked language (#455).

Each is stored in English as before (Propose reads the reasons when it asks again, the report's
facts and memory read the plan and the diagnosis) and carries the template it was made from;
`tests/conftest.py` holds every refusal any test produces to its template's English.
"""

import json

import pytest
from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from test_execute import Env, engine, restart
from test_propose import acting, setup
from test_propose import restart as plan_restart
from test_report import run as run_report
from test_report import setup as setup_report
from test_report import written

from bleep import prompts
from bleep.graph.deps import StaticSettings
from bleep.graph.nodes import diagnose, propose
from bleep.graph.planted import planted_shown
from bleep.graph.shown import english
from bleep.guard.plan_check import check_plan
from bleep.incidents.shown import diagnosis_in, event_in, proposal_in, report_in
from bleep.schemas.graph import Diagnosis, PlanStep, Proposal, VerificationPlan
from bleep.schemas.shown import Shown
from bleep.watcher import detector, manager
from graph_env import graph_env

# Each fixed text and the words it was stored in before #455: a model reads them, so they must
# not change. The plan check's reasons are held to theirs by `tests/conftest.py`.
BEFORE = [
    (
        propose.SHOWN[propose.NO_DIAGNOSIS],
        "Bleep has no diagnosis to act on, so it proposes no action.",
    ),
    (propose.SHOWN[propose.NO_PLAN], "The model gave no usable plan, so Bleep proposes no action."),
    (
        propose.SHOWN[propose.BUDGET],
        "The incident's token budget or the day's cost cap is spent, so Bleep proposes no action.",
    ),
    (
        propose.SHOWN[propose.REFUSED],
        "Bleep's own checks refused the proposed plan twice, so nothing will run.",
    ),
    (
        propose.SHOWN[propose.INSTRUCT],
        "You can tell Bleep what to do instead and it will plan again with that; it can also let "
        "this go. Nothing can be approved from a refused plan.",
    ),
    (
        propose.SHOWN[propose.BY_HAND],
        "A container that works is acted on only when it is this incident's own or when you name "
        "it in an instruction. If it is the one at fault, name it, or act on it by hand.",
    ),
    (
        propose.SHOWN[propose.BY_HAND_ONLY],
        "A container that works is acted on only when it is this incident's own. If it is the one "
        "at fault, act on it by hand.",
    ),
    (
        propose.SHOWN[propose.STOP_BY_HAND],
        "A container is stopped only when this incident's signals name it or you name it in an "
        "instruction. If it should stop, name it, or stop it by hand.",
    ),
    (
        propose.SHOWN[propose.STOP_BY_HAND_ONLY],
        "A container is stopped only when this incident's signals name it. If it should stop, "
        "stop it by hand.",
    ),
    (
        propose.SHOWN[propose.UNCLEAR_BY_HAND],
        "Bleep never acts on a container whose name is also the start of another container's id, "
        "and an instruction cannot change that. Act on it by hand, or rename it (`docker rename`) "
        "so its name reads as no container's id.",
    ),
    (
        propose.SHOWN[propose.HIDDEN_BY_HAND],
        "A step runs only when you can read every param of it, and Bleep hides anything that "
        "looks like a secret. If the change is needed, make it by hand.",
    ),
    (diagnose.FALLBACK_SHOWN, "I could not form a diagnosis"),
    (detector.MODEL_DOWN, "The container died and its restart policy brought it back."),
    (detector.NO_LOGS, "No logs were available to explain the restart."),
    (manager.NO_EXPLANATION_SHOWN, "No explanation could be generated for this restart."),
    (manager.CAP_SPENT_SHOWN, "No explanation: the daily cost cap is spent."),
    (
        manager.HELD_LOOP_SHOWN,
        "It died again in its crash loop, and its restart policy brought it back.",
    ),
]


@pytest.mark.parametrize(("text", "before"), BEFORE)
def test_a_text_in_place_of_the_models_is_stored_in_the_words_it_had_before(text, before):
    assert english(text) == before
    assert prompts.show(text, "de") != before


def test_each_constant_a_test_or_the_report_reads_is_its_templates_english():
    for text, shown in propose.SHOWN.items():
        assert english(shown) == text
    assert english(diagnose.FALLBACK_SHOWN) == diagnose.FALLBACK_CAUSE


async def test_the_plan_checks_reasons_read_in_german(tmp_path):
    env, _ = await setup(tmp_path)
    plan = Proposal(
        kind="act",
        steps=[
            plan_restart("bleep-backend-1"),
            plan_restart("nope"),
            PlanStep(action="docker.restart", host_id="b", target="shop-web-1"),
            plan_restart("shop-proxy-1"),
        ],
        reason="restart it",
        verification=VerificationPlan(urls=["http://evil/"], containers=["shop_web"]),
    )

    found = check_plan(plan, env.registry, {"docker.restart"}, "a", incident_containers=[])

    assert [prompts.show(v.shown, "de") for v in found] == [
        "der Plan hat 4 Schritte; erlaubt sind höchstens 3",
        "Schritt 1 (docker.restart bleep-backend-1 auf a): bleep-backend-1 ist einer von Bleeps "
        "eigenen Containern",
        "Schritt 2 (docker.restart nope auf a): kein Container mit diesem Namen auf diesem Host",
        "Schritt 3 (docker.restart shop-web-1 auf b): zielt auf einen anderen Host; dieser "
        "Vorfall gehört zu a",
        "Schritt 4 (docker.restart shop-proxy-1 auf a): shop-proxy-1 läuft ohne Health-Check, "
        "und er gehört nicht zu den Containern dieses Vorfalls; an einem Container, der "
        "funktioniert, etwas zu tun, behebt diesen Vorfall nicht",
        "Prüf-URL http://evil/: ist keine Health-Check-URL von Host a in seinen Einstellungen; "
        "nur diese lassen sich prüfen",
        "Prüf-Container shop_web: kein Container mit diesem Namen auf Host a",
    ]
    assert found[1].text == (
        "step 1 (docker.restart bleep-backend-1 on a): bleep-backend-1 is one of Bleep's own "
        "containers"
    )


async def test_a_refused_plan_is_stored_and_asked_again_in_the_same_words_in_every_language(
    tmp_path,
):
    stored, prompt, again, logged, published = {}, {}, {}, {}, {}
    for language in ("en", "de"):
        env, state = await setup(
            tmp_path / language, acting(plan_restart("shop-proxy-1")), acting(plan_restart("x"))
        )
        env.deps.settings_store = StaticSettings(written_in=language)
        update = await propose.make(env.deps)(state)
        stored[language] = update["proposal"]
        seen = env.models.model.seen
        prompt[language] = seen[0][0].text
        again[language] = [m.text for m in seen[1][1:]]  # the plan and the reasons, after it
        logged[language] = [(kind, detail) for _, kind, detail in env.guard_log.records]
        events = await env.bus.history(state["incident_id"])
        published[language] = [e.payload for e in events if e.kind == "proposal"]

    assert stored["de"] == stored["en"] and stored["en"].refused
    assert again["de"] == again["en"] and logged["de"] == logged["en"]
    assert published["de"] == published["en"]
    rule = prompts.load("graph/propose_de").strip()
    assert " ".join(prompt["de"].replace(rule, "").split()) == " ".join(prompt["en"].split())
    assert "läuft ohne Health-Check" not in " ".join(again["de"])
    assert stored["en"].refusal_reasons == [
        "step 1 (docker.restart x on a): no container by that name on this host"
    ]


async def test_a_refused_plan_reads_in_german_through_the_view_and_as_stored_in_english(tmp_path):
    env, state = await setup(
        tmp_path, acting(plan_restart("shop-proxy-1")), acting(plan_restart("shop-proxy-1"))
    )
    stored = (await propose.make(env.deps)(state))["proposal"]

    german = proposal_in(stored, "de")

    assert german.reason == (
        "Bleeps eigene Prüfungen haben den vorgeschlagenen Plan zweimal zurückgewiesen, also läuft "
        "nichts."
    )
    assert german.refusal_reasons == [
        "Schritt 1 (docker.restart shop-proxy-1 auf a): shop-proxy-1 läuft ohne Health-Check, "
        "und er gehört nicht zu den Containern dieses Vorfalls; an einem Container, der "
        "funktioniert, etwas zu tun, behebt diesen Vorfall nicht"
    ]
    assert german.human_instructions.split("\n") == [
        german.reason,
        "Warum:",
        f"- {german.refusal_reasons[0]}",
        "Der zurückgewiesene Plan:",
        "1. docker.restart shop-proxy-1 auf a",
        "Die Begründung des Modells: restart it",
        prompts.show(Shown.of("proposal/by_hand"), "de"),
        prompts.show(Shown.of("proposal/instruct"), "de"),
    ]
    in_english = proposal_in(stored, "en")
    assert (in_english.reason, in_english.human_instructions, in_english.refusal_reasons) == (
        stored.reason,
        stored.human_instructions,
        stored.refusal_reasons,
    )


async def test_a_plan_with_no_diagnosis_or_no_usable_plan_gives_its_reason_in_german(tmp_path):
    env, state = await setup(tmp_path, *[AIMessage("not json")] * 4)
    none = (await propose.make(env.deps)(state | {"diagnosis": None}))["proposal"]
    unusable = (await propose.make(env.deps)(state))["proposal"]

    assert (none.reason, unusable.reason) == (propose.NO_DIAGNOSIS, propose.NO_PLAN)
    assert proposal_in(none, "de").reason == (
        "Bleep hat keine Diagnose, nach der es handeln kann, also schlägt es nichts vor."
    )
    assert proposal_in(unusable, "de").reason == (
        "Das Modell hat keinen brauchbaren Plan geliefert, also schlägt Bleep nichts vor."
    )


async def test_a_plan_the_model_wrote_is_shown_as_it_wrote_it(tmp_path):
    env, state = await setup(tmp_path, acting(plan_restart("shop-web-1")))
    planned = (await propose.make(env.deps)(state))["proposal"]

    assert planned.shown == {} and planned.refusal_reasons_shown == []
    assert proposal_in(planned, "de").reason == "restart it"


def test_a_refusal_whose_stored_english_was_changed_shows_as_stored():
    reason = Shown.of("proposal/refused")
    stored = Proposal(
        kind="no_action",
        reason="Bleep's own checks refused the plan.",  # not the template's English any more
        shown={"reason": reason},
        refusal_reasons=["one", "two"],
        refusal_reasons_shown=[Shown.of("refusal/no_container")],  # one for two: drifted
    )

    german = proposal_in(stored, "de")

    assert (german.reason, german.refusal_reasons) == (stored.reason, ["one", "two"])


def test_a_plan_stored_before_455_loads_and_shows_as_stored():
    old = Proposal.model_validate(
        {"kind": "no_action", "reason": propose.REFUSED, "refusal_reasons": ["step 1 (x): no"]}
    )

    german = proposal_in(old, "de")

    assert (german.reason, german.refusal_reasons) == (propose.REFUSED, ["step 1 (x): no"])


def test_the_diagnosis_schema_the_model_fills_has_no_shown_text():
    schema = json.dumps(convert_to_openai_tool(diagnose._Draft))
    assert "shown" not in schema
    assert "root_cause" in schema and "evidence" in schema


async def test_the_fallback_diagnosis_reads_in_german_and_is_stored_in_english(tmp_path):
    from test_diagnose import setup as setup_diagnose

    stored = {}
    for language in ("en", "de"):
        env, state = await setup_diagnose(
            tmp_path / language, [AIMessage("no json"), AIMessage("{")]
        )
        env.deps.settings_store = StaticSettings(written_in=language)
        stored[language] = (await diagnose.make(env.deps)(state))["diagnosis"]

    assert stored["de"] == stored["en"]
    assert stored["en"].root_cause == "I could not form a diagnosis"
    german = diagnosis_in(stored["en"], "de")
    assert german.root_cause == "Ich konnte keine Diagnose stellen"
    assert [e.detail for e in german.evidence] == ["could not read: logs of shop-db-1: timeout"]


def test_bleeps_own_evidence_line_reads_in_german_and_the_models_as_written():
    line = planted_shown(["logs"])
    found = Diagnosis(
        root_cause="the database refused its password",
        category="config",
        confidence=0.9,
        evidence=[
            {"source": "gather", "detail": "shop-web-1 exited with code 1"},
            {"source": "bleep", "detail": english(line), "shown": line.stored()},
        ],
    )

    german = diagnosis_in(found, "de")

    assert german.root_cause == found.root_cause
    assert [e.detail for e in german.evidence] == [
        "shop-web-1 exited with code 1",
        "Bleep hat in den Container-Logs Text gefunden, der versucht hat, ihm Befehle zu geben, "
        "und den es ignoriert hat; prüfe, wer dort schreiben darf.",
    ]


async def test_the_report_shows_the_fallback_cause_in_german_when_the_model_wrote_none(tmp_path):
    fallback = Diagnosis(
        root_cause=diagnose.FALLBACK_CAUSE,
        category="unknown",
        confidence=0.0,
        unsure=True,
        shown=diagnose.FALLBACK_SHOWN,
    )
    stored = {}
    for language in ("en", "de"):
        env, state, _ = await setup_report(tmp_path / language, written(cause=""))
        env.deps.settings_store = StaticSettings(written_in=language)
        state |= {"diagnosis": fallback, "proposal": propose._no_action(propose.NO_PLAN)}
        stored[language] = (await run_report(env, state))["report"]

    assert stored["en"].cause == "I could not form a diagnosis"
    assert stored["de"].cause == stored["en"].cause
    assert report_in(stored["de"], "de").cause == "Ich konnte keine Diagnose stellen"
    assert report_in(stored["en"], "en").cause == stored["en"].cause


async def test_a_cause_the_model_wrote_is_never_shown_as_the_fallback(tmp_path):
    fallback = Diagnosis(
        root_cause=diagnose.FALLBACK_CAUSE,
        category="unknown",
        confidence=0.0,
        shown=diagnose.FALLBACK_SHOWN,
    )
    env, state, _ = await setup_report(tmp_path, written(cause="The disk is full."))
    state |= {"diagnosis": fallback, "proposal": propose._no_action(propose.NO_PLAN)}

    stored = (await run_report(env, state))["report"]

    assert "cause" not in stored.shown
    assert report_in(stored, "de").cause == "The disk is full."


async def test_a_refusal_at_run_time_is_noticed_in_english_and_read_in_german(tmp_path):
    env = Env(await graph_env(tmp_path, engine=engine()))
    state = await env.approve(restart("shop-db-1"), restart("shop-cache-1"))
    env.replace("shop-cache-1", labels={"bleep.self": "true"})
    await env.deps.registry.refresh("a")

    await env.run(state)

    [event] = [e for e in await env.env.bus.history(state["incident_id"]) if e.kind == "notice"]
    assert event.payload["text"] == (
        "The plan did not run, Bleep's checks refused it: step 2 (docker.restart shop-cache-1 on "
        "a): shop-cache-1 is one of Bleep's own containers. Planning again."
    )
    assert event_in(event, "de").payload["text"] == (
        "Der Plan lief nicht, Bleeps Prüfungen haben ihn zurückgewiesen: Schritt 2 "
        "(docker.restart shop-cache-1 auf a): shop-cache-1 ist einer von Bleeps eigenen "
        "Containern. Ich plane neu."
    )


async def test_an_approval_that_does_not_cover_a_step_is_noticed_in_german(tmp_path):
    env = Env(await graph_env(tmp_path, engine=engine()))
    state = await env.approve(restart("shop-db-1"))
    state["grant"] = state["grant"].model_copy(update={"token": "0" * 64})

    await env.run(state)

    notice = [e for e in await env.env.bus.history(state["incident_id"]) if e.kind == "notice"]
    assert notice[-1].payload["text"] == (
        "The plan did not run, Bleep's checks refused it: step 1 (docker.restart shop-db-1 on a): "
        "the approval does not cover it (the grant's token does not match). Planning again."
    )
    assert event_in(notice[-1], "de").payload["text"] == (
        "Der Plan lief nicht, Bleeps Prüfungen haben ihn zurückgewiesen: Schritt 1 "
        "(docker.restart shop-db-1 auf a): die Freigabe deckt ihn nicht ab (das Token der "
        "Freigabe stimmt nicht). Ich plane neu."
    )
