"""An expected step can expect the params of the step that proposes it, as `arguments` is for an
`mcp.call`: the `key` of a `coolify.set_env` (#359)."""

import pytest
from pydantic import ValidationError

from bleep.schemas.graph import PlanStep, Proposal
from evals.report import _expected_step
from evals.scenarios import Expected, ExpectedStep, score_action

APP = "storefront"


def step(action: str, **params: str) -> PlanStep:
    return PlanStep(action=action, host_id="demo", target=APP, params=params)


def plan(*steps: PlanStep) -> Proposal:
    return Proposal(kind="act", steps=list(steps), reason="set it", verification={})


def expected(ordered: bool = True, **params: str) -> Expected:
    set_env = {"action": "coolify.set_env", "target": APP, "params": params}
    return Expected.model_validate(
        {
            "root_cause": "SESSION_TTL_S is not set",
            "category": "config",
            "action": {
                "kind": "act",
                "ordered": ordered,
                "steps": [set_env, {"action": "coolify.redeploy", "target": APP}],
            },
        }
    )


SET = step("coolify.set_env", key="SESSION_TTL_S", value="3600")
REDEPLOY = step("coolify.redeploy")


def test_a_set_env_with_the_expected_key_is_correct_whatever_its_value():
    other = step("coolify.set_env", key="SESSION_TTL_S", value="86400")
    for proposed in (SET, other):
        score = score_action(expected(key="SESSION_TTL_S"), plan(proposed, REDEPLOY))
        assert score.action_correct and not score.unnecessary_action


def test_a_set_env_with_another_key_is_wrong_and_unnecessary():
    wrong = step("coolify.set_env", key="SESSION_TTL", value="3600")
    score = score_action(expected(key="SESSION_TTL_S"), plan(wrong, REDEPLOY))
    assert not score.action_correct and score.unnecessary_action


def test_the_expected_value_is_held_too_when_it_is_given():
    wanted = expected(key="SESSION_TTL_S", value="3600")
    assert score_action(wanted, plan(SET, REDEPLOY)).action_correct
    wrong = step("coolify.set_env", key="SESSION_TTL_S", value="1")
    assert not score_action(wanted, plan(wrong, REDEPLOY)).action_correct


def test_the_redeploy_before_the_set_env_is_wrong_in_an_ordered_plan_only():
    reversed_plan = plan(REDEPLOY, SET)
    assert not score_action(expected(key="SESSION_TTL_S"), reversed_plan).action_correct
    assert score_action(expected(False, key="SESSION_TTL_S"), reversed_plan).action_correct


def test_a_step_without_the_expected_params_does_not_match():
    bare = ExpectedStep(action="coolify.set_env", target=APP, params={"key": "SESSION_TTL_S"})
    assert bare.matches(SET)
    assert not bare.matches(PlanStep(action="coolify.set_env", host_id="demo", target=APP))


def test_a_step_that_expects_no_params_matches_whatever_params_it_has():
    plain = ExpectedStep(action="coolify.set_env", target=APP)
    assert plain.matches(SET)


@pytest.mark.parametrize(
    ("step", "why"),
    [
        ({"action": "coolify.redeploy", "target": APP, "params": {"key": "A"}}, "no params"),
        ({"action": "docker.restart", "target": APP, "params": {"key": "A"}}, "no params"),
        (
            {
                "action": "mcp.call",
                "target": APP,
                "tool": "t",
                "params": {"tool": "t"},
            },
            "no params",
        ),
        ({"action": "coolify.set_env", "target": APP, "params": {"name": "A"}}, "params names"),
        ({"action": "coolify.set_env", "target": APP, "params": {}}, "params names"),
    ],
)
def test_params_are_expected_only_of_a_step_that_takes_them(step, why):
    with pytest.raises(ValidationError, match=why):
        ExpectedStep.model_validate(step)


def test_a_report_shows_the_params_an_expected_step_holds():
    with_key = ExpectedStep(action="coolify.set_env", target=APP, params={"key": "SESSION_TTL_S"})
    assert (
        _expected_step(with_key)
        == 'coolify.set_env storefront with params {"key": "SESSION_TTL_S"}'
    )
    bare = ExpectedStep(action="coolify.redeploy", target=APP)
    assert _expected_step(bare) == "coolify.redeploy storefront"
