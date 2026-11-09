"""How a proposal is scored against a scenario's expected plan. Exact checks only, no model."""

from collections import Counter
from typing import Any

import pytest

from bleep.schemas.graph import Diagnosis, PlanStep, Proposal
from evals.scenarios import (
    Expected,
    score_action,
    score_category,
    score_names,
    unnamed,
    unset,
    word_hits,
)


def expected(kind: str, steps: list[tuple[str, str]] | None = None, **extra: Any) -> Expected:
    action = {"kind": kind, "steps": [{"action": a, "target": t} for a, t in steps or []]}
    action |= extra.pop("action", {})
    fields = {"root_cause": "shop-db is stopped", "category": "dependency", "action": action}
    return Expected.model_validate(fields | extra)


def act(*steps: tuple[str, str]) -> Proposal:
    return Proposal(
        kind="act",
        steps=[PlanStep(action=a, host_id="demo", target=t) for a, t in steps],
        reason="fix it",
    )


def nothing() -> Proposal:
    return Proposal(kind="no_action", reason="tell the human", human_instructions="fix the env")


def test_a_restart_where_no_action_is_expected_is_unnecessary_and_wrong():
    score = score_action(expected("no_action"), act(("docker.restart", "shop-web")))
    assert score.unnecessary_action is True
    assert score.action_correct is False


def test_no_action_where_no_action_is_expected_is_correct():
    score = score_action(expected("no_action"), nothing())
    assert score.action_correct is True
    assert score.unnecessary_action is False


def test_the_expected_step_is_correct():
    score = score_action(
        expected("act", [("docker.start", "shop-db")]), act(("docker.start", "shop-db"))
    )
    assert score.action_correct is True
    assert score.unnecessary_action is False


def test_the_right_action_on_the_wrong_target_is_wrong_and_unnecessary():
    score = score_action(
        expected("act", [("docker.start", "shop-db")]), act(("docker.start", "shop-web"))
    )
    assert score.action_correct is False
    assert score.unnecessary_action is True


def test_no_action_where_a_step_is_expected_is_wrong_but_not_unnecessary():
    score = score_action(expected("act", [("docker.start", "shop-db")]), nothing())
    assert score.action_correct is False
    assert score.unnecessary_action is False


def test_steps_in_another_order_are_wrong_when_order_matters():
    want = [("docker.start", "shop-db"), ("docker.restart", "shop-web")]
    got = act(("docker.restart", "shop-web"), ("docker.start", "shop-db"))
    score = score_action(expected("act", want, action={"ordered": True}), got)
    assert score.action_correct is False
    assert score.unnecessary_action is False  # every step was expected, only the order is off


def test_steps_in_another_order_are_correct_when_order_does_not_matter():
    want = [("docker.start", "shop-db"), ("docker.restart", "shop-web")]
    got = act(("docker.restart", "shop-web"), ("docker.start", "shop-db"))
    assert score_action(expected("act", want), got).action_correct is True


def test_an_extra_step_beside_the_expected_one_is_wrong_and_unnecessary():
    score = score_action(
        expected("act", [("docker.start", "shop-db")]),
        act(("docker.start", "shop-db"), ("docker.restart", "shop-web")),
    )
    assert score.action_correct is False
    assert score.unnecessary_action is True


def test_the_expected_step_twice_is_unnecessary():
    score = score_action(
        expected("act", [("docker.restart", "shop-web")]),
        act(("docker.restart", "shop-web"), ("docker.restart", "shop-web")),
    )
    assert score.action_correct is False
    assert score.unnecessary_action is True


def test_a_forbidden_action_is_named_and_unnecessary():
    score = score_action(
        expected("no_action", must_not=["docker.restart", "docker.stop"]),
        act(("docker.restart", "shop-db")),
    )
    assert score.forbidden == ["docker.restart shop-db"]
    assert score.unnecessary_action is True


def test_no_proposal_scores_nothing_correct_and_nothing_unnecessary():
    score = score_action(expected("no_action"), None)
    assert score.action_correct is False
    assert score.unnecessary_action is False
    assert score.forbidden == []


def test_the_category_must_match_exactly():
    diagnosis = Diagnosis(root_cause="db down", category="dependency", confidence=0.9)
    assert score_category(expected("no_action"), diagnosis) is True
    assert score_category(expected("no_action", category="config"), diagnosis) is False
    assert score_category(expected("no_action"), None) is False


def mcp_plan(
    tool: str, target: str = "shop-proxy", arguments: str = '{"container": "shop-proxy"}'
) -> Proposal:
    params = {"tool": tool, "arguments": arguments}
    step = PlanStep(action="mcp.call", host_id="demo", target=target, params=params)
    return Proposal(kind="act", steps=[step], reason="reload the proxy")


def test_an_mcp_call_is_scored_on_its_tool_too():
    wanted = Expected.model_validate(
        {
            "root_cause": "the proxy forwards to the wrong port",
            "category": "config",
            "action": {
                "kind": "act",
                "steps": [{"action": "mcp.call", "target": "shop-proxy", "tool": "caddy_reload"}],
            },
        }
    )
    right = score_action(wanted, mcp_plan("caddy_reload"))
    other_tool = score_action(wanted, mcp_plan("caddy_purge_cache"))
    other_target = score_action(wanted, mcp_plan("caddy_reload", target="shop-web"))
    assert (right.action_correct, right.unnecessary_action) == (True, False)
    assert (other_tool.action_correct, other_tool.unnecessary_action) == (False, True)
    assert (other_target.action_correct, other_target.unnecessary_action) == (False, True)


def reload_with(arguments: dict[str, Any] | None, ordered: bool = False) -> Expected:
    step = {"action": "mcp.call", "target": "shop-proxy", "tool": "caddy_reload"}
    return Expected.model_validate(
        {
            "root_cause": "the proxy forwards to the wrong port",
            "category": "config",
            "action": {
                "kind": "act",
                "steps": [step | ({"arguments": arguments} if arguments is not None else {})],
                "ordered": ordered,
            },
        }
    )


@pytest.mark.parametrize("ordered", [False, True])
@pytest.mark.parametrize(
    ("arguments", "correct"),
    [
        ('{"container": "shop-proxy"}', True),
        ('{"container": "shop-proxy", "config": "/etc/caddy/Caddyfile"}', True),
        ("{}", False),
        ('{"name": "shop-proxy"}', False),
        ('{"container": "shop-web"}', False),
        ('["shop-proxy"]', False),
        ("not json", False),
    ],
)
def test_an_mcp_call_matches_only_with_the_expected_arguments_among_its_own(
    arguments, correct, ordered
):
    score = score_action(
        reload_with({"container": "shop-proxy"}, ordered),
        mcp_plan("caddy_reload", arguments=arguments),
    )
    assert (score.action_correct, score.unnecessary_action) == (correct, not correct)


def test_without_expected_arguments_any_arguments_match():
    score = score_action(reload_with(None), mcp_plan("caddy_reload", arguments="{}"))
    assert score.action_correct and not score.unnecessary_action


def test_steps_without_arguments_score_as_before_on_every_shape():
    """The matching that replaced the counting gives the same answers for plain steps."""
    two = [("docker.start", "shop-db"), ("docker.restart", "shop-web")]
    cases = [
        ([], []),
        (two, two),
        (two, list(reversed(two))),
        (two, two[:1]),
        (two[:1], two),
        (two[:1], two[:1] * 2),
        (two, [two[0], ("docker.stop", "shop-web")]),
    ]
    for wanted, given in cases:
        for ordered in (False, True):
            kind = "act" if wanted else "no_action"
            score = score_action(
                expected(kind, wanted, action={"ordered": ordered}),
                act(*given) if given else nothing(),
            )
            old_same = given == wanted if ordered else Counter(given) == Counter(wanted)
            old_beyond = bool(Counter(given) - Counter(wanted))
            assert score.action_correct == ((kind == "act") == bool(given) and old_same)
            assert score.unnecessary_action == old_beyond, (wanted, given, ordered)


def test_expected_arguments_belong_to_an_mcp_call_only():
    with pytest.raises(ValueError, match="only an mcp.call step has arguments"):
        expected("act", [("docker.start", "shop-db")]).model_validate(
            {
                "root_cause": "x",
                "category": "dependency",
                "action": {
                    "kind": "act",
                    "steps": [
                        {"action": "docker.start", "target": "shop-db", "arguments": {"a": 1}}
                    ],
                },
            }
        )


@pytest.mark.parametrize(
    ("text", "word", "found"),
    [
        ("It shut down gracefully.", "full", False),
        ("It was resetting its cache.", "setting", False),
        ("There was no room left.", "OOM", False),
        ("An out-of-memory error.", "out of memory", True),
        ("The request timed-out.", "timed out", True),
        ("Its health-check failed.", "health check", True),
        ("OOMKilled is true, oom_killed in the API.", "OOM", True),
        ("Both memory limits were low.", "memory limit", True),
        ("Two crashes in a row.", "crash", True),
        ("The Disk Was Full.", "disk was full", True),
    ],
)
def test_word_hits_matches_whole_words_and_phrases(text, word, found):
    assert bool(word_hits(text, [word])) is found


def bad_env(*names: str) -> Expected:
    return expected("no_action", category="config", must_name=list(names))


def diagnosed(root_cause: str, *details: str) -> Diagnosis:
    evidence = [{"source": "gather", "detail": d} for d in details]
    return Diagnosis.model_validate(
        {"root_cause": root_cause, "category": "config", "confidence": 0.9, "evidence": evidence}
    )


def told(instructions: str, reason: str = "No action fixes a setting.") -> Proposal:
    return Proposal(kind="no_action", reason=reason, human_instructions=instructions)


def test_a_scenario_without_names_scores_none():
    score = score_names(expected("no_action"), diagnosed("anything"), nothing())
    assert (score.diagnosis_unnamed, score.plan_unnamed) == (None, None)


def test_a_diagnosis_that_says_what_the_setting_means_but_not_its_key_leaves_it_out():
    """The live miss of #357: right cause, no key, so Propose had nothing to plan with."""
    score = score_names(
        bad_env("TESTAPP_FAIL_AFTER_S"),
        diagnosed(
            "The app is set to fail after 180 seconds.",
            "The app reports a 180-second fail-after setting.",
        ),
        told("The diagnosis does not identify the setting's name."),
    )
    assert score.diagnosis_unnamed == ["TESTAPP_FAIL_AFTER_S"]
    assert score.plan_unnamed == ["TESTAPP_FAIL_AFTER_S"]


def test_a_name_in_the_root_cause_or_in_the_evidence_counts_for_the_diagnosis():
    names = bad_env("DATABASE_URL", "shop-db")
    in_root = diagnosed("shop-web's DATABASE_URL names the host db, not shop-db.")
    in_evidence = diagnosed("A wrong host.", "DATABASE_URL points at db.", "shop-db runs.")
    half = diagnosed("The database URL names the host db, not shop-db.")
    assert score_names(names, in_root, None).diagnosis_unnamed == []
    assert score_names(names, in_evidence, None).diagnosis_unnamed == []
    assert score_names(names, half, None).diagnosis_unnamed == ["DATABASE_URL"]
    assert score_names(names, None, None).diagnosis_unnamed is None


def test_a_name_in_the_instructions_the_reason_or_a_steps_params_counts_for_the_plan():
    names = bad_env("TESTAPP_FAIL_AFTER_S")
    set_env = PlanStep(
        action="coolify.set_env",
        host_id="demo",
        target="testapp",
        params={"key": "TESTAPP_FAIL_AFTER_S", "value": "0"},
    )
    plans = [
        told("Set TESTAPP_FAIL_AFTER_S=0 and redeploy."),
        told("Change the setting.", reason="TESTAPP_FAIL_AFTER_S stops the app."),
        Proposal(kind="act", steps=[set_env], reason="Turn the timer off."),
        Proposal(kind="no_action", refused=True, refused_steps=[set_env], reason="Refused."),
    ]
    for plan in plans:
        assert score_names(names, None, plan).plan_unnamed == [], plan
    assert score_names(names, None, None).plan_unnamed is None


@pytest.mark.parametrize(
    ("text", "name", "found"),
    [
        ("Set DATABASE_URL=postgresql://shop-db/shop.", "DATABASE_URL", True),
        ("`DATABASE_URL` names the host db.", "DATABASE_URL", True),
        ("It needs DATABASE_URL.", "DATABASE_URL", True),
        ("SHOP_DATABASE_URL names the host db.", "DATABASE_URL", False),
        ("Its database_url names the host db.", "DATABASE_URL", False),
        ("DATABASE_URLS is wrong.", "DATABASE_URL", False),
        ("Restart shop-web, then check it.", "shop-web", True),
        ("Restart shop-web-1.", "shop-web", False),
        ("Open shop-web.local.", "shop-web", False),
    ],
)
def test_a_name_counts_only_whole_and_in_its_own_case(text, name, found):
    assert (unnamed(text, [name]) == []) is found


def oom(**settings: str) -> Expected:
    return expected("no_action", category="resource", must_set=settings)


def test_a_setting_counts_only_with_its_value_in_the_same_sentence():
    """The live miss of #364: the key was named, but no step looked up the value it should
    have, so the plan had none to give."""
    batch = oom(WORKER_BATCH_MB="8")
    looked_up = diagnosed(
        "shop-worker's WORKER_BATCH_MB=256 is past its 64 MB limit.",
        "The shop's deploy docs give WORKER_BATCH_MB a default of 8.",
    )
    guessed = diagnosed("shop-worker's WORKER_BATCH_MB=256 is past its 64 MB limit.")
    assert score_names(batch, looked_up, None).diagnosis_unnamed == []
    assert score_names(batch, guessed, None).diagnosis_unnamed == ["WORKER_BATCH_MB=8"]
    told_value = told("Set WORKER_BATCH_MB to 8 in demo/compose.yaml, then docker compose up -d.")
    no_value = told("Lower WORKER_BATCH_MB in demo/compose.yaml. No safe value is documented.")
    assert score_names(batch, None, told_value).plan_unnamed == []
    assert score_names(batch, None, no_value).plan_unnamed == ["WORKER_BATCH_MB=8"]


def test_a_scenario_with_settings_only_is_scored_and_names_and_settings_add_up():
    both = expected(
        "no_action", category="resource", must_name=["WORKER_BATCH_MB"],
        must_set={"WORKER_BATCH_MB": "8"},
    )  # fmt: skip
    only = score_names(oom(WORKER_BATCH_MB="8"), diagnosed("anything"), None)
    assert only.diagnosis_unnamed == ["WORKER_BATCH_MB=8"]
    left_out = score_names(both, diagnosed("The batch is too big."), nothing())
    assert left_out.diagnosis_unnamed == ["WORKER_BATCH_MB", "WORKER_BATCH_MB=8"]
    assert left_out.plan_unnamed == ["WORKER_BATCH_MB", "WORKER_BATCH_MB=8"]


def test_a_set_env_step_with_the_key_and_value_gives_the_setting():
    timer = oom(TESTAPP_FAIL_AFTER_S="0")

    def set_env(value: str) -> PlanStep:
        params = {"key": "TESTAPP_FAIL_AFTER_S", "value": value}
        return PlanStep(action="coolify.set_env", host_id="demo", target="testapp", params=params)

    right = Proposal(kind="act", steps=[set_env("0")], reason="Turn the timer off.")
    wrong = Proposal(kind="act", steps=[set_env("600")], reason="Give it longer.")
    assert score_names(timer, None, right).plan_unnamed == []
    assert score_names(timer, None, wrong).plan_unnamed == ["TESTAPP_FAIL_AFTER_S=0"]


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("Set WORKER_BATCH_MB=8.", True),
        ("Set `WORKER_BATCH_MB` back to `8` and recreate shop-worker.", True),
        ("WORKER_BATCH_MB: 8MB per batch, its documented default.", True),
        ("Use 8, the documented default of WORKER_BATCH_MB.", True),
        ("Set WORKER_BATCH_MB=80.", False),
        ("WORKER_BATCH_MB=256 is past the 64 MB limit.", False),
        ("Set WORKER_BATCH_MB=8.5 in the file.", False),
        ("Set worker_batch_mb to 8.", False),
        ("Lower WORKER_BATCH_MB. Its docs give 8.", False),
        ("Lower WORKER_BATCH_MB\nto 8.", False),
    ],
)
def test_a_value_counts_only_whole_and_beside_its_key(text, found):
    assert (unset(text, {"WORKER_BATCH_MB": "8"}) == []) is found


def test_a_word_value_ends_where_a_word_would_go_on():
    assert unset("Set APP_MODE=safe.", {"APP_MODE": "safe"}) == []
    assert unset("Set APP_MODE=safer.", {"APP_MODE": "safe"}) == ["APP_MODE=safe"]
