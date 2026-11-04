"""Scenarios, what one run of them found, and the exact checks on a proposal.

A scenario is a demo fault with a known root cause and the plan Bleep should propose for it, one
YAML file per scenario in `evals/scenarios/`. The root cause is scored by the judge
(`evals/judge.py`); the category and the plan are scored here, exactly. See docs/design/evals.md.
"""

import re
import typing
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from bleep.guard.plan_check import mcp_arguments
from bleep.schemas.graph import Action, Diagnosis, PlanStep, Proposal
from bleep.schemas.incidents import Subject
from evals.github import GithubFixtureError, load_github_fixture
from evals.mcp_server import McpFixtureError, load_mcp_fixture

SCENARIOS_DIR = Path(__file__).parent / "scenarios"
CATEGORIES: tuple[str, ...] = typing.get_args(Diagnosis.model_fields["category"].annotation)
MAX_STEPS = 3  # the plan check's limit (guard/plan_check.py)
# How a run ended early: out of time, out of the model's context, or any other error.
Failure = Literal["timeout", "context_length", "error"]


class ScenarioError(ValueError):
    """A scenario file is missing, malformed, or not the one asked for."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExpectedStep(_Strict):
    action: Action
    target: str  # the container name, exactly as the plan must name it
    tool: str | None = None  # an mcp.call's tool, which it must name; no other action has one
    # Arguments an mcp.call's must contain, each with this value; others may come beside them.
    arguments: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _tool_only_for_mcp_call(self) -> Self:
        if (self.action == "mcp.call") != (self.tool is not None):
            raise ValueError("an mcp.call step names its tool, and no other step has one")
        if self.arguments is not None and self.action != "mcp.call":
            raise ValueError("only an mcp.call step has arguments")
        return self

    @property
    def key(self) -> tuple[str, str, str | None]:
        return (self.action, self.target, self.tool)

    def matches(self, step: PlanStep) -> bool:
        """The step is this one: action, target and tool, and its arguments a JSON object that
        holds every expected argument with its value."""
        if step_key(step) != self.key:
            return False
        if self.arguments is None:
            return True
        given = mcp_arguments(step)
        return given is not None and all(
            k in given and given[k] == v for k, v in self.arguments.items()
        )


def step_key(step: PlanStep) -> tuple[str, str, str | None]:
    """What a plan's step is scored on: its action and target, and an mcp.call's tool."""
    tool = step.params.get("tool") if step.action == "mcp.call" else None
    return (step.action, step.target, tool)


class ExpectedAction(_Strict):
    """The plan Bleep should propose. `ordered` makes the step order part of the answer."""

    kind: Literal["act", "no_action"]
    steps: list[ExpectedStep] = Field(default_factory=list)
    ordered: bool = False

    @model_validator(mode="after")
    def _steps_fit_kind(self) -> Self:
        if self.kind == "no_action" and self.steps:
            raise ValueError("a no_action plan has no steps")
        if self.kind == "act" and not 1 <= len(self.steps) <= MAX_STEPS:
            raise ValueError(f"an act plan has one to three steps, not {len(self.steps)}")
        return self

    @property
    def keys(self) -> list[tuple[str, str, str | None]]:
        return [s.key for s in self.steps]


class Expected(_Strict):
    root_cause: str = Field(min_length=1)  # one sentence the judge compares the diagnosis with
    category: str
    action: ExpectedAction
    must_not: list[Action] = Field(default_factory=list)  # actions that must not be proposed

    @field_validator("category")
    @classmethod
    def _known_category(cls, value: str) -> str:
        if value not in CATEGORIES:
            raise ValueError(f"category must be one of {', '.join(CATEGORIES)}")
        return value

    @model_validator(mode="after")
    def _must_not_excludes_the_answer(self) -> Self:
        clash = sorted({a for a, _, _ in self.action.keys} & set(self.must_not))
        if clash:
            raise ValueError(f"must_not forbids the expected action {', '.join(clash)}")
        return self


class Scenario(_Strict):
    id: str
    fault: str  # the name `demo/fault.sh` switches
    subject: Subject  # the incident the fault must open
    expected: Expected
    timeout_s: int = Field(600, gt=0)  # from the fault on to the end of Propose
    # A fixture in evals/fixtures/github/ that the GitHub tools read; without one they are absent.
    github: str | None = None
    # A fixture in evals/fixtures/mcp/ served as the MCP plugin's server, the plugin switched on
    # with its settings; without one the plugin is off.
    mcp: str | None = None


def load_scenario(path: Path) -> Scenario:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        scenario = Scenario.model_validate(data)
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise ScenarioError(f"scenario {path}: {error}") from error
    if scenario.id != path.stem:
        raise ScenarioError(f"scenario {path}: its id {scenario.id!r} must be its file name")
    if scenario.github is not None:
        try:
            load_github_fixture(scenario.github)
        except GithubFixtureError as error:
            raise ScenarioError(f"scenario {path}: {error}") from error
    if scenario.mcp is not None:
        try:
            load_mcp_fixture(scenario.mcp)
        except McpFixtureError as error:
            raise ScenarioError(f"scenario {path}: {error}") from error
    return scenario


def load_scenarios(directory: Path = SCENARIOS_DIR, only: str | None = None) -> list[Scenario]:
    """Every scenario in `directory` by id, or just the one named `only`."""
    paths = sorted(directory.glob("*.yaml"))
    if only is not None:
        chosen = [p for p in paths if p.stem == only]
        if not chosen:
            known = ", ".join(p.stem for p in paths) or "none"
            raise ScenarioError(f"no scenario {only!r} in {directory}; known: {known}")
        paths = chosen
    return [load_scenario(p) for p in paths]


class ActionScore(BaseModel):
    """`action_correct`: the plan is exactly the expected one. `unnecessary_action`: the plan
    acts beyond it (a step not expected, one too many, or one `must_not` names). `forbidden`
    names the proposed steps whose action `must_not` names, as "action target"."""

    action_correct: bool
    unnecessary_action: bool
    forbidden: list[str] = Field(default_factory=list)


def score_action(expected: Expected, proposal: Proposal | None) -> ActionScore:
    """Exact match on kind, action and target, an `mcp.call`'s tool and the arguments the
    scenario expects of it; the order counts only when `ordered` is set.

    No proposal (the run failed or timed out) is neither correct nor unnecessary.
    """
    if proposal is None:
        return ActionScore(action_correct=False, unnecessary_action=False)
    proposed = list(proposal.steps) if proposal.kind == "act" else []
    wanted = expected.action.steps
    matched = _matched(proposed, wanted)
    if expected.action.ordered:
        same = len(proposed) == len(wanted) and all(
            w.matches(p) for p, w in zip(proposed, wanted, strict=True)
        )
    else:
        same = len(proposed) == len(wanted) == matched
    forbidden = [f"{s.action} {s.target}" for s in proposed if s.action in expected.must_not]
    return ActionScore(
        action_correct=proposal.kind == expected.action.kind and same,
        unnecessary_action=matched < len(proposed) or bool(forbidden),
        forbidden=forbidden,
    )


def _matched(proposed: list[PlanStep], wanted: list[ExpectedStep]) -> int:
    """How many proposed steps can each be paired with a different expected step they match,
    at most (a maximum bipartite matching; a plan has at most three steps)."""
    paired: dict[int, int] = {}  # expected step -> proposed step

    def place(p: int, seen: set[int]) -> bool:
        for w, step in enumerate(wanted):
            if w in seen or not step.matches(proposed[p]):
                continue
            seen.add(w)
            if w not in paired or place(paired[w], seen):
                paired[w] = p
                return True
        return False

    return sum(place(p, set()) for p in range(len(proposed)))


def word_hits(text: str, words: Sequence[str]) -> list[str]:
    """The `words` that `text` uses as whole words or phrases, in any case: `full` is not found
    in "gracefully", nor `setting` in "resetting"."""
    return [w for w in words if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", text, re.IGNORECASE)]


def score_category(expected: Expected, diagnosis: Diagnosis | None) -> bool:
    return diagnosis is not None and diagnosis.category == expected.category


class ScenarioResult(BaseModel):
    """One run of one scenario: what Bleep diagnosed and proposed, and how it scored.

    `steps` counts the model's tool calls while investigating. `tokens` and `usd` are the model
    under test's, for the whole run; `peak_prompt_tokens` is its largest single prompt, the
    number that meets a small context window first. `structured_fallbacks` counts the model
    calls that gave no structured output through tools, so Bleep asked for JSON instead. Times
    are seconds from the fault going on.
    `root_cause_score` is the judge's 0-1 score, None when there was nothing to judge, the judge
    failed, or the grade is left to a human (the `manual` judge) and not applied yet. A run that
    ended early is a failed run: `failure` says how (`timeout`, `context_length` when a model
    call reached its length limit before it answered, or `error`), and `error` says what
    happened.
    """

    id: str
    run: int = 1
    model: str
    judge: str
    incident_id: str | None = None
    diagnosis: Diagnosis | None = None
    proposal: Proposal | None = None
    root_cause_score: float | None = None
    root_cause_pass: bool = False
    judge_reason: str | None = None
    category_correct: bool = False
    action_correct: bool = False
    unnecessary_action: bool = False
    forbidden: list[str] = Field(default_factory=list)
    steps: int = 0
    tokens: int = 0
    peak_prompt_tokens: int = 0
    structured_fallbacks: int = 0
    usd: float | None = None
    judge_usd: float | None = None
    seconds_to_incident: float | None = None
    seconds_to_diagnosis: float | None = None
    failure: Failure | None = None
    error: str | None = None

    @property
    def run_id(self) -> str:
        """`<scenario>/<run>`, the name a grade by hand gives the run."""
        return f"{self.id}/{self.run}"
