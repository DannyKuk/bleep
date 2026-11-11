"""Verify's check details and the report's steps inside Telegram messages, in the picked language
(#454)."""

from test_threads import env, report  # noqa: F401  (the fixture is the thread tests')

from bleep.graph.shown import english
from bleep.schemas.graph import OUTCOME_TEXT, Check, ReportStep, VerificationResult
from bleep.schemas.shown import Shown

EXITED = Check(
    kind="container",
    target="shop-web",
    ok=False,
    detail="is exited",
    shown=Shown.of("verify/is_exited"),
)
STOPPED = Check(
    kind="container",
    target="shop-db (stopped by the plan)",
    ok=True,
    detail="is exited",
    target_shown=Shown.of("verify/target_stopped", name="shop-db"),
    shown=Shown.of("verify/is_exited"),
)
ANSWERING = Check(
    kind="url",
    target="http://shop/health",
    ok=True,
    detail="answering 200",
    shown=Shown.of("verify/answering", status="200"),
)
STEP = Shown.of(
    "step/line",
    action="docker.start",
    target="shop-db",
    host="laptop",
    status=Shown.of("step/done"),
)
NOTE = Shown.of(
    "planted/sentence", where=Shown.of("planted/where_logs"), end=Shown.of("planted/end")
)


async def test_a_check_that_did_not_hold_lists_its_details_in_the_picked_language(env):  # noqa: F811
    env.notifier.language = lambda: "de"
    incident = await env.open()
    await env.notifier.incident_opened(incident)

    await env.notifier.verification(
        incident, VerificationResult(ok=False, checks=[EXITED, STOPPED])
    )
    await env.notifier.verification(incident, VerificationResult(ok=False))

    _, held, nothing = env.chat.sent()
    assert held["text"].split("\n") == [
        "*❌ Hat nicht gehalten*",
        "FEHLGESCHLAGEN Container shop\\-web: ist beendet",
        "ok Container shop\\-db \\(vom Plan gestoppt\\): ist beendet",
    ]
    assert nothing["text"].split("\n")[1] == "Es konnte nichts geprüft werden\\."


async def test_in_english_a_check_that_did_not_hold_lists_its_details_as_before(env):  # noqa: F811
    incident = await env.open()
    await env.notifier.incident_opened(incident)

    await env.notifier.verification(
        incident, VerificationResult(ok=False, checks=[EXITED, STOPPED])
    )

    assert env.chat.sent()[-1]["text"].split("\n")[1:] == [
        "FAILED container shop\\-web: is exited",
        "ok container shop\\-db \\(stopped by the plan\\): is exited",
    ]


async def test_an_end_names_what_ran_and_what_was_checked_in_the_picked_language(env):  # noqa: F811
    env.notifier.language = lambda: "de"
    incident = await env.open(name="shop")
    await env.notifier.incident_opened(incident)
    payload = {"ok": True, "attempt": 1, "checks": [ANSWERING.stored()]}
    await env.bus.publish(incident.id, "verification", payload)

    await env.notifier.report(
        incident,
        report(
            OUTCOME_TEXT["resolved"],
            action_taken="docker.start shop-db on laptop: done",
            steps=[ReportStep(text=english(STEP), status="done", shown=STEP)],
            follow_ups=["Bleep found text somewhere", "Set a restart policy."],
            shown_follow_ups={0: NOTE},
        ),
    )

    text = env.chat.sent()[-1]["text"]
    assert "Ausgeführt: docker\\.start shop\\-db auf laptop: erledigt" in text
    assert "Geprüft: http://shop/health antwortet mit 200" in text
    assert "\\- Bleep hat in den Container\\-Logs Text gefunden, der versucht hat" in text
    assert "\\- Set a restart policy\\." in text  # the model's own words, as written


async def test_in_english_an_end_names_what_ran_and_what_was_checked_as_before(env):  # noqa: F811
    incident = await env.open(name="shop")
    await env.notifier.incident_opened(incident)
    payload = {"ok": True, "attempt": 1, "checks": [ANSWERING.stored()]}
    await env.bus.publish(incident.id, "verification", payload)

    await env.notifier.report(
        incident,
        report(
            OUTCOME_TEXT["resolved"],
            action_taken="docker.start shop-db on laptop: done",
            steps=[ReportStep(text=english(STEP), status="done", shown=STEP)],
        ),
    )

    text = env.chat.sent()[-1]["text"]
    assert "Ran: docker\\.start shop\\-db on laptop: done" in text
    assert "Checked: http://shop/health answering 200" in text


async def test_an_end_whose_texts_drifted_keeps_the_stored_english(env):  # noqa: F811
    env.notifier.language = lambda: "de"
    incident = await env.open(name="shop")
    await env.notifier.incident_opened(incident)
    drifted = ANSWERING.stored() | {"shown": {"name": "verify/renamed_later"}}
    await env.bus.publish(
        incident.id, "verification", {"ok": True, "attempt": 1, "checks": [drifted]}
    )

    await env.notifier.report(
        incident,
        report(
            OUTCOME_TEXT["resolved"],
            action_taken="docker.start shop-db on laptop: done",
            steps=[
                ReportStep(
                    text="docker.start shop-db on laptop: done",
                    status="done",
                    shown=Shown(name="step/renamed_later"),
                )
            ],
        ),
    )

    text = env.chat.sent()[-1]["text"]
    assert "Ausgeführt: docker\\.start shop\\-db on laptop: done" in text
    assert "Geprüft: http://shop/health answering 200" in text
