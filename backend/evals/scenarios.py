"""Scenarios, what one run of them found, and the exact checks on a proposal.

A scenario is a demo fault with a known root cause and the plan Bleep should propose for it, one
YAML file per scenario in `evals/scenarios/`. The root cause is scored by the judge
(`evals/judge.py`); the category and the plan are scored here, exactly. See docs/design/evals.md.
"""

import json
import re
import typing
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from bleep.guard.plan_check import ACTION_PARAMS, mcp_arguments
from bleep.plugins.mcp_url import Purpose
from bleep.schemas.graph import Action, Diagnosis, PlanStep, Proposal, Report
from bleep.schemas.incidents import Subject
from evals.coolify import CoolifyFixtureError, load_coolify_fixture
from evals.github import GithubFixtureError, load_github_fixture
from evals.mcp_server import McpFixtureError, load_mcp_fixture
from evals.scripted_host import ScriptedHost

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
    # Params a coolify.set_env's own must hold, each with this value (its `key`); others may
    # come beside them.
    params: dict[str, str] | None = None

    @model_validator(mode="after")
    def _tool_only_for_mcp_call(self) -> Self:
        if (self.action == "mcp.call") != (self.tool is not None):
            raise ValueError("an mcp.call step names its tool, and no other step has one")
        if self.arguments is not None and self.action != "mcp.call":
            raise ValueError("only an mcp.call step has arguments")
        return self

    @model_validator(mode="after")
    def _params_are_ones_the_step_takes(self) -> Self:
        if self.params is None:
            return self
        takes = set() if self.action == "mcp.call" else ACTION_PARAMS.get(self.action, set())
        if not takes:
            raise ValueError(f"{self.action} has no params to expect; an mcp.call has arguments")
        if not self.params or not set(self.params) <= takes:
            raise ValueError(f"params names some of {', '.join(sorted(takes))}")
        return self

    @property
    def key(self) -> tuple[str, str, str | None]:
        return (self.action, self.target, self.tool)

    def matches(self, step: PlanStep) -> bool:
        """The step is this one: action, target and tool, its params holding every expected
        param with its value, and its arguments a JSON object that holds every expected argument
        with its value."""
        if step_key(step) != self.key:
            return False
        if self.params is not None and any(step.params.get(k) != v for k, v in self.params.items()):
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
    # What the fix needs named, exactly as the host names it (an environment variable's key, a
    # file, a container): the diagnosis and the plan must each name every one (#357).
    must_name: list[str] = Field(default_factory=list)
    # Settings the fix gives a value, key to value, exactly as the service's docs write them: the
    # diagnosis and the plan must each give the key that value (#364).
    must_set: dict[str, str] = Field(default_factory=dict)

    @field_validator("category")
    @classmethod
    def _known_category(cls, value: str) -> str:
        if value not in CATEGORIES:
            raise ValueError(f"category must be one of {', '.join(CATEGORIES)}")
        return value

    @field_validator("must_name")
    @classmethod
    def _plain_names(cls, value: list[str]) -> list[str]:
        if any(not name or name != name.strip() for name in value):
            raise ValueError("must_name holds names without spaces around them, none empty")
        return value

    @field_validator("must_set")
    @classmethod
    def _plain_settings(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not part or part != part.strip() for pair in value.items() for part in pair):
            raise ValueError("must_set holds keys and values without spaces around them")
        return value

    @model_validator(mode="after")
    def _must_not_excludes_the_answer(self) -> Self:
        clash = sorted({a for a, _, _ in self.action.keys} & set(self.must_not))
        if clash:
            raise ValueError(f"must_not forbids the expected action {', '.join(clash)}")
        return self


class Scenario(_Strict):
    """A fault the demo stack breaks with, a look on request at a service that is fine (`look`,
    #410): the run opens the incident as a person's tap does, nothing switched, or a host in
    memory (`scripted`, #359): the run opens the incident from its scripted signals."""

    id: str
    fault: str | None = None  # the name `demo/fault.sh` switches
    look: bool = False
    subject: Subject  # the incident the fault, or the look, must open
    expected: Expected
    timeout_s: int = Field(600, gt=0)  # from the fault on to the end of Propose
    # A fixture in evals/fixtures/github/ that the GitHub tools read; without one they are absent.
    github: str | None = None
    # A fixture in evals/fixtures/mcp/ served as the MCP plugin's server, the plugin switched on
    # with its settings; without one the plugin is off.
    mcp: str | None = None
    # What the plugin's `purpose` setting says that server is for (#370); empty leaves it unset.
    mcp_purpose: Purpose = ""
    # The service has no URL in Bleep's settings for this run (#445): the hosts file's URLs are
    # dropped from the host the run watches, so a look finds none to check. Looks only.
    no_urls: bool = False
    # A host in memory instead of the demo stack (#359): its containers, and the signals that open
    # the incident, which the run opens itself. No fault, no Docker, no demo shop.
    scripted: ScriptedHost | None = None
    # A fixture in evals/fixtures/coolify/ served as the Coolify API, the plugin switched on with
    # its act tools, and the fixture's container, in the scripted host, labelled as its
    # application's; without one the plugin is off.
    coolify: str | None = None

    @model_validator(mode="after")
    def _a_purpose_needs_a_server(self) -> Self:
        if self.mcp_purpose and self.mcp is None:
            raise ValueError("an mcp_purpose needs an mcp fixture")
        return self

    @model_validator(mode="after")
    def _one_plugin_server(self) -> Self:
        if self.coolify is not None and self.mcp is not None:
            raise ValueError("a scenario serves an mcp fixture or a coolify one, not both")
        if self.coolify is not None and self.scripted is None:
            raise ValueError("a coolify fixture labels a container of a scripted host")
        return self

    @model_validator(mode="after")
    def _a_fault_a_look_or_a_scripted_host(self) -> Self:
        if [self.fault is not None, self.look, self.scripted is not None].count(True) != 1:
            raise ValueError(
                "a scenario switches a fault or asks for a look or runs on a scripted host, "
                "one of the three"
            )
        if self.no_urls and not self.look:
            raise ValueError("no_urls only changes what a look checks")
        if self.look and self.subject.kind != "service":
            raise ValueError("a look is asked for a service, never a whole host")
        return self

    @property
    def opener(self) -> str:
        """What opens the scenario's incident, as a report names it."""
        if self.scripted is not None:
            return "the signals of a scripted host"
        return "a look on request" if self.look else f"the fault `{self.fault}`"


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
    if scenario.coolify is not None:
        assert scenario.scripted is not None  # `_one_plugin_server`
        try:
            container = load_coolify_fixture(scenario.coolify).container
        except CoolifyFixtureError as error:
            raise ScenarioError(f"scenario {path}: {error}") from error
        if container not in {c.name for c in scenario.scripted.containers}:
            raise ScenarioError(
                f"scenario {path}: the coolify fixture's container {container!r} is not in "
                "the scripted host"
            )
    return scenario


def load_scenarios(directory: Path = SCENARIOS_DIR, only: str | None = None) -> list[Scenario]:
    """Every scenario in `directory` by id, or those `only` names (ids, comma-separated), in
    file order."""
    paths = sorted(directory.glob("*.yaml"))
    if only is not None:
        wanted = {name.strip() for name in only.split(",") if name.strip()}
        known = ", ".join(p.stem for p in paths) or "none"
        if not wanted:
            raise ScenarioError(f"--only names no scenario; known: {known}")
        unknown = sorted(wanted - {p.stem for p in paths})
        if unknown:
            raise ScenarioError(
                f"no scenario {', '.join(repr(name) for name in unknown)} in {directory}; "
                f"known: {known}"
            )
        paths = [p for p in paths if p.stem in wanted]
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
    """The `words` that `text` uses as whole words or phrases, in any case and with an optional
    plural `s` or `es`: `full` is not found in "gracefully", nor `setting` in "resetting". The
    words of a phrase may be joined by spaces, hyphens or underscores, so "out of memory" is
    found in "out-of-memory" and "oom killed" in "oom_killed"."""
    return [w for w in words if _word_pattern(w).search(text)]


def without_words(text: str, words: Sequence[str]) -> str:
    """`text` with every use of `words` that `word_hits` finds taken out."""
    for word in words:
        text = _word_pattern(word).sub(" ", text)
    return text


def _word_pattern(word: str) -> re.Pattern[str]:
    parts = [re.escape(part) for part in re.split(r"[\s_-]+", word.strip()) if part]
    body = r"[\s_-]+".join(parts)
    return re.compile(rf"(?<![^\W_]){body}(?:e?s)?(?![^\W_])", re.IGNORECASE)


def score_category(expected: Expected, diagnosis: Diagnosis | None) -> bool:
    return diagnosis is not None and diagnosis.category == expected.category


class NameScore(BaseModel):
    """The scenario's `must_name` names, and its `must_set` settings as `KEY=VALUE`, that the
    diagnosis, and the plan, leave out; None when the scenario lists none or there is no
    diagnosis (or no plan) to read."""

    diagnosis_unnamed: list[str] | None = None
    plan_unnamed: list[str] | None = None


def score_names(
    expected: Expected, diagnosis: Diagnosis | None, proposal: Proposal | None
) -> NameScore:
    """Which of the names the fix needs the diagnosis (its root cause and evidence) and the plan
    (its reason, its instructions to the human, and its steps' targets and params, as a
    `coolify.set_env` key) leave out, and which `must_set` settings they do not give their value
    (`unset`; a `coolify.set_env` step with that key and value gives it too). Propose reads the
    diagnosis and not the logs, so a name or a value the diagnosis drops cannot reach the plan
    (#357, #364)."""
    names, settings = expected.must_name, expected.must_set
    if not names and not settings:
        return NameScore()
    score = NameScore()
    if diagnosis is not None:
        said = "\n".join([diagnosis.root_cause, *(e.detail for e in diagnosis.evidence)])
        score.diagnosis_unnamed = unnamed(said, names) + unset(said, settings)
    if proposal is not None:
        steps = [*proposal.steps, *proposal.refused_steps]
        said = "\n".join([proposal.reason, proposal.human_instructions or ""])
        targets = "\n".join(text for s in steps for text in (s.target, *s.params.values()))
        set_env = {(s.params.get("key"), s.params.get("value")) for s in steps}
        wanted = {k: v for k, v in settings.items() if (k, v) not in set_env}
        score.plan_unnamed = unnamed(f"{said}\n{targets}", names) + unset(said, wanted)
    return score


def unnamed(text: str, names: Sequence[str]) -> list[str]:
    """The `names` that `text` does not use whole and in the same case: `DATABASE_URL` is not
    named by `SHOP_DATABASE_URL` or `database_url`, nor `shop-web` by `shop-web-1`; a period or
    comma after a name ends it."""
    return [n for n in names if not _name_pattern(n).search(text)]


def _name_pattern(name: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w.-]){re.escape(name)}(?![\w-])(?!\.\w)")


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n")


def unset(text: str, settings: dict[str, str]) -> list[str]:
    """The `settings`, as `KEY=VALUE`, that no sentence of `text` gives: one that names the key
    (as `unnamed` reads a name) and the value whole, in either order (`WORKER_BATCH_MB=8`, "set
    WORKER_BATCH_MB back to 8 MB", "8, the documented default of WORKER_BATCH_MB"). `8` is not in
    `80`, `256` or `8.5`. A value in the sentence after the key does not count."""
    sentences = _SENTENCE_END.split(text)
    return [
        f"{key}={value}"
        for key, value in settings.items()
        if not any(
            _name_pattern(key).search(s) and _value_pattern(value).search(s) for s in sentences
        )
    ]


def _value_pattern(value: str) -> re.Pattern[str]:
    # A number may carry its unit (`8MB`); anything else ends where a word would go on.
    after = r"(?!\d)(?!\.\d)" if value[-1].isdigit() else r"(?![\w-])"
    return re.compile(rf"(?<![\w.]){re.escape(value)}{after}")


ARGS_SHOWN = 80  # characters of a tool call's arguments a result keeps


class ToolCallRecord(BaseModel):
    """One tool call of a run: its name and its arguments as JSON, cut short."""

    name: str
    args: str = ""

    @classmethod
    def of(cls, name: object, args: object) -> Self:
        text = json.dumps(args, sort_keys=True, default=str) if args else ""
        if len(text) > ARGS_SHOWN:
            text = text[: ARGS_SHOWN - 1] + "…"
        return cls(name=str(name), args=text)


class ScenarioResult(BaseModel):
    """One run of one scenario: what Bleep diagnosed and proposed, and how it scored.

    `steps` counts the model's tool calls while investigating, and `tool_calls` lists them in
    order (runs from before it was kept have none). `tokens` and `usd` are the model
    under test's, for the whole run; `peak_prompt_tokens` is its largest single prompt, the
    number that meets a small context window first. `structured_fallbacks` counts the model
    calls that gave no structured output through tools, so Bleep asked for JSON instead. Times
    are seconds from the fault going on.
    `diagnosis_unnamed` and `plan_unnamed` are the scenario's `must_name` names, and its
    `must_set` settings as `KEY=VALUE`, each left out (`score_names`), None when not scored.
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
    report: Report | None = None  # a look's that proposed no action; no other run reaches Report
    root_cause_score: float | None = None
    root_cause_pass: bool = False
    judge_reason: str | None = None
    category_correct: bool = False
    action_correct: bool = False
    unnecessary_action: bool = False
    forbidden: list[str] = Field(default_factory=list)
    diagnosis_unnamed: list[str] | None = None
    plan_unnamed: list[str] | None = None
    steps: int = 0
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    tokens: int = 0
    peak_prompt_tokens: int = 0
    structured_fallbacks: int = 0
    usd: float | None = None
    judge_usd: float | None = None
    judge_attempts: int = 1  # model calls the judge needed; files from before it count as one
    seconds_to_incident: float | None = None
    seconds_to_diagnosis: float | None = None
    failure: Failure | None = None
    error: str | None = None

    @property
    def run_id(self) -> str:
        """`<scenario>/<run>`, the name a grade by hand gives the run."""
        return f"{self.id}/{self.run}"
