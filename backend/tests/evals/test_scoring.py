"""How a proposal is scored against a scenario's expected plan. Exact checks only, no model."""

from collections import Counter
from typing import Any

import pytest

from bleep.schemas.graph import Diagnosis, PlanStep, Proposal
from evals.scenarios import Expected, score_action, score_category, word_hits


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
