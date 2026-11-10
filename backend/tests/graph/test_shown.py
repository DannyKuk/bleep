"""The fixed texts Bleep stores, in English as before they became templates (#427)."""

import pytest

from bleep import prompts
from bleep.graph.nodes import execute, report
from bleep.graph.replan import PLANNING_AGAIN
from bleep.graph.shown import english, shown_payload
from bleep.incidents import expectations, runner
from bleep.notify.telegram import ON_IT_NOTICE
from bleep.schemas.shown import Shown
from bleep.watcher import manager

# Each text and the words it was stored in before #427: a model reads them (the report's facts,
# the chat's snapshot), so they must not change.
BEFORE = [
    (
        runner.QUIET_RUN_NOTICE,
        "Closed: the service was made quiet while I worked on it, so I stopped here.",
    ),
    (
        runner.QUIET_RUN_NOTICE_HOST,
        "Closed: the host was made quiet while I worked on it, so I stopped here.",
    ),
    (
        runner.WAITING_NOTICE,
        "The host does not answer, so there is nothing I can look at: every check would fail. "
        "I wait for it and close this incident when it answers again.",
    ),
    (
        runner.BACK_NOTICE,
        "Closed: the host answers again. It recovered on its own; nothing was changed.",
    ),
    (
        runner.ANSWERED_NOTICE,
        "Closed: the host answered again before I got to it. It recovered on its own; nothing "
        "was changed.",
    ),
    (
        runner.QUIET_RUN_NOTICE.then(runner.MAY_HAVE_RUN),
        "Closed: the service was made quiet while I worked on it, so I stopped here. A step of "
        "the approved plan may already have run; see the actions above.",
    ),
    (
        Shown.of("notice/run_error", error="ValueError").then(
            Shown.of("notice/run_error_repeat", count="3", since="10 Nov 12:00 UTC")
        ),
        "The run stopped on an error (ValueError); see the backend log. This incident is seen 3 "
        "times since 10 Nov 12:00 UTC.",
    ),
    (
        Shown.of("notice/model_paused", reason="timeout", minutes="5"),
        "The model is not answering (timeout). The run is paused and tries again from where it "
        "stopped, first in 5 minutes, then less often.",
    ),
    (
        runner.STILL_PAUSED,
        "The run has been paused for an hour: the model is still not answering.",
    ),
    (
        Shown.of("notice/stuck", minutes="31", status="investigating"),
        "This incident has not moved for 31 minutes (status investigating). Check the run; it may "
        "be waiting on a slow model or host.",
    ),
    (expectations.CLOSED_NOTICE, "Closed: you said this is expected."),
    (expectations.DECISION_TEXT, "This is expected."),
    (
        expectations.CLOSED_MEANWHILE,
        "The incident was closed meanwhile, before anyone answered its plan.",
    ),
    (
        manager.REMOVED_NOTICE,
        "Closed: the host was removed from Bleep on the dashboard, so it is not watched.",
    ),
    (
        manager.MOVED_NOTICE,
        "The host's address, port or user was changed on the dashboard: what Bleep reads from "
        "here on comes from the new connection, which may be another machine.",
    ),
    (
        Shown.of("notice/urls_unreachable", urls="http://a, http://b"),
        "URL checks failing while the host is unreachable: http://a, http://b.",
    ),
    (PLANNING_AGAIN, "Planning again."),
    (
        Shown.of("notice/stopped", limit="3"),
        "Bleep stopped after 3 new plans in this incident; nothing more will run.",
    ),
    (
        Shown.of("notice/not_sent"),
        "The plan could not be sent to a channel; answer it here.",
    ),
    (
        Shown.of("notice/budget_daily", cap="$1.00"),
        "Investigation stopped: the daily model cost cap ($1.00) is spent.",
    ),
    (
        Shown.of("notice/budget_tokens", tokens="200,000"),
        "Investigation stopped: this incident used its 200,000 model tokens.",
    ),
    (
        Shown.of("notice/refused", reasons="step 1 (docker.stop db on a): not allowed"),
        "The plan did not run, Bleep's checks refused it: step 1 (docker.stop db on a): not "
        "allowed.",
    ),
    (
        execute.NOT_APPROVED,
        "The plan did not run, it is not the plan the human approved.",
    ),
    (ON_IT_NOTICE, "Snoozed from Telegram: you said you are on it."),
    (Shown.of("expected/not_offered"), "Nothing here can be marked as expected now."),
    (
        Shown.of("expected/no_service"),
        "Bleep does not see that service now; nothing was recorded.",
    ),
    (
        Shown.of("expected/not_recorded"),
        "Bleep could not record that; nothing was changed.",
    ),
    (
        Shown.of("expected/said", sentence=Shown.of("expected/may_sleep", name="nas")).then(
            Shown.of("expected/not_closed")
        ),
        "You said this is expected. nas may sleep: I open nothing while it does not answer, and "
        "show it as asleep. The incident was not closed here; see its page.",
    ),
    (
        Shown.of("expected/exits", jobs="backup, cron", name="shop"),
        "backup, cron on shop is expected to exit: I stay quiet when it exits with 0, and a crash "
        "is still an incident.",
    ),
    (
        Shown.of("expected/decided_on", channel=Shown.of("channel/on_dashboard")),
        "The plan was already decided on the dashboard, so the incident stays open.",
    ),
    (
        Shown.of("expected/decided"),
        "The plan was already decided, so the incident stays open.",
    ),
    (
        Shown.of("outcome/not_verified", outcome=Shown.of("outcome/budget")),
        "Stopped, the model budget is spent; an earlier action was not verified",
    ),
    (Shown.of("outcome/healthy_anyway"), report.HEALTHY_ANYWAY),
    (Shown.of("report/not_determined"), "Not determined."),
]


@pytest.mark.parametrize(("text", "before"), BEFORE)
def test_a_fixed_text_is_stored_in_the_words_it_had_before_it_became_a_template(text, before):
    assert english(text) == before


def test_the_outcomes_read_as_they_did_before():
    assert report.OUTCOME_TEXT == {
        "resolved": "Fixed and verified",
        "healthy": "Healthy when checked, nothing to do",
        "unverified": "Action taken, not verified",
        "rejected": "Rejected, nothing was done",
        "no_action": "No action proposed, nothing was done",
        "budget": "Stopped, the model budget is spent",
        "replan_limit": "Stopped after too many new plans",
    }
    assert report.HEALTHY_ANYWAY == "Healthy again (verified); no step of the plan succeeded"


@pytest.mark.parametrize(
    ("kind", "via", "let_go", "before"),
    [
        ("approve", "telegram", False, "Approved on Telegram"),
        ("reject", "dashboard", False, "Rejected on the dashboard"),
        ("reject", "telegram", True, "Let go on Telegram"),
        ("approve", None, False, "Approved on a channel"),
        ("instruct", "dashboard", False, "Instruction from the dashboard: use the backup"),
        ("instruct", "slack", False, "Instruction from a channel: use the backup"),
    ],
)
def test_a_decision_reads_as_it_did_before(kind, via, let_go, before):
    assert report.decision_text(kind, via, "use the backup", let_go=let_go) == before


def test_a_notice_payload_keeps_the_english_text_and_the_shown_text_beside_it():
    text = Shown.of("notice/left_alone", name="shop")
    stored = shown_payload(text)
    assert stored["text"] == "Closed: you told me to leave shop alone."
    assert Shown.in_payload(stored) == text
    assert prompts.show(text, "de") == "Geschlossen: Du hast mir gesagt, shop in Ruhe zu lassen."


def test_a_nested_text_and_a_list_are_shown_in_the_same_language():
    where = Shown.of("target/where", target="web-1", host="demo")
    changes = Shown.joined(
        [
            Shown.of("target/recreated", where=where),
            Shown.of("target/state", where=where, why=execute.GONE),
        ],
        "; ",
    )
    stale = Shown.of("notice/stale", changes=changes).then(PLANNING_AGAIN)
    assert english(stale) == (
        "The plan did not run, the state changed since approval: web-1 on demo was recreated; "
        "web-1 on demo is gone. Planning again."
    )
    assert prompts.show(stale, "de") == (
        "Der Plan lief nicht, der Zustand hat sich seit der Freigabe geändert: web-1 auf demo "
        "wurde neu erstellt; web-1 auf demo ist weg. Ich plane neu."
    )
