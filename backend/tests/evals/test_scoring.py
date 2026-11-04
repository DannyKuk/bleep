"""How a proposal is scored against a scenario's expected plan. Exact checks only, no model."""

from typing import Any

from bleep.schemas.graph import Diagnosis, PlanStep, Proposal
from evals.scenarios import Expected, score_action, score_category


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


def mcp_plan(tool: str, target: str = "shop-proxy") -> Proposal:
    params = {"tool": tool, "arguments": '{"container": "shop-proxy"}'}
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
