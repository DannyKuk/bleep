"""The root cause judge: DeepEval's GEval asks a model other than the one under test whether a
diagnosis names the scenario's expected root cause, and scores it from 0 to 1.

The judge is named by `EVAL_JUDGE_MODEL`: an OpenRouter model id (`openai/gpt-6-luna` by default,
`OPENROUTER_API_KEY` from the environment or `.env`), `claude-code[:<model>]`, which runs the
local Claude Code CLI with no tools on the human's own login, or `manual`, which calls no model
and leaves each root cause for a grade by hand (`python -m evals.report --grades`). The harness
refuses a judge that is the model under test. See docs/design/evals.md.
"""

import asyncio
import os
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import cache
from typing import Any, Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openrouter import ChatOpenRouter
from pydantic import BaseModel

from bleep import prompts
from bleep.schemas.graph import Diagnosis
from bleep.schemas.settings import ModelSettings

# Read by DeepEval when it is imported: no telemetry, and no .env of its own (the harness reads
# the repo's .env itself and hands the judge only the key it needs).
os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] = "YES"
os.environ["DEEPEVAL_DISABLE_DOTENV"] = "1"

DEFAULT_JUDGE = "openai/gpt-6-luna"
CLAUDE_CODE = "claude-code"
MANUAL = "manual"  # no judge call: the root cause is graded by hand afterwards
JUDGE_ATTEMPTS = 3  # an answer GEval cannot read, or a failed call, is asked for again
PASS_SCORE = 0.7  # the steps give a middling score to the right component for a wrong reason
CLI_TIMEOUT_S = 300


class JudgeError(Exception):
    """The judge cannot be built or did not answer. After retries it carries what they cost."""

    def __init__(self, message: str = "", attempts: int = 1, usd: float | None = None) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.usd = usd


class SameModel(JudgeError):
    """The judge would grade its own diagnosis."""


@dataclass(frozen=True)
class Completion:
    text: str
    usd: float | None = None  # None when the backend does not bill per call or did not say


Complete = Callable[[str], Awaitable[Completion]]
# (argv, stdin) -> (exit code, stdout, stderr)
CliRunner = Callable[[list[str], str], Awaitable[tuple[int, str, str]]]


class Judgement(BaseModel):
    score: float  # 0-1
    passed: bool  # score >= PASS_SCORE
    reason: str
    usd: float | None = None  # all attempts added up
    attempts: int = 1


class Judge(Protocol):
    name: str

    async def judge(self, expected: str, diagnosis: Diagnosis) -> Judgement | None:
        """How well `diagnosis` names the `expected` root cause; None leaves it to a human."""
        ...


class ManualJudge:
    """Calls no model: the root cause stays pending until a grades file is applied to the raw
    results (`python -m evals.report --grades`)."""

    name = MANUAL

    async def judge(self, expected: str, diagnosis: Diagnosis) -> None:
        return None


def model_under_test(models: ModelSettings) -> str:
    """The model Bleep's main role calls with these settings."""
    return models.local_model if models.provider == "local" else models.model


def _base_name(model: str) -> str:
    """The model's own name: no provider prefix, no OpenRouter variant tag (`:free`, `:nitro`)."""
    name = model.removeprefix(CLAUDE_CODE).removeprefix(":")
    return name.rsplit("/", 1)[-1].split(":", 1)[0].strip().lower()


def check_distinct(judge: str, tested: str) -> None:
    """Refuse a judge that is the model under test, under any provider's prefix."""
    name = _base_name(judge)
    if name and name == _base_name(tested):
        raise SameModel(
            f"the judge {judge!r} is the model under test {tested!r}; set EVAL_JUDGE_MODEL to "
            "another model"
        )


def evaluation_steps() -> list[str]:
    """GEval's evaluation steps, one per `- ` line of the prompt file."""
    text = prompts.load("evals/root_cause")
    return [line[2:].strip() for line in text.splitlines() if line.startswith("- ")]


def describe(diagnosis: Diagnosis) -> str:
    """The diagnosis as the judge reads it: the cause, its category and the evidence."""
    lines = [f"Root cause: {diagnosis.root_cause}", f"Category: {diagnosis.category}"]
    lines += [f"Evidence ({e.source}): {e.detail}" for e in diagnosis.evidence]
    return "\n".join(lines)


def openrouter(model: str, key: str) -> Complete:
    chat = ChatOpenRouter(model=model, temperature=0, api_key=key)
    system = prompts.load("evals/judge_system")

    async def complete(prompt: str) -> Completion:
        reply = await chat.ainvoke([SystemMessage(system), HumanMessage(prompt)])
        cost = reply.response_metadata.get("cost")
        return Completion(reply.text, float(cost) if isinstance(cost, int | float) else None)

    return complete


async def _run_cli(argv: list[str], stdin: str) -> tuple[int, str, str]:
    """Run the CLI in an empty folder, so no project's CLAUDE.md reaches the judge."""
    with tempfile.TemporaryDirectory() as folder:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=folder,
        )
        try:
            out, err = await asyncio.wait_for(process.communicate(stdin.encode()), CLI_TIMEOUT_S)
        except TimeoutError:
            process.kill()
            await process.wait()
            return -1, "", f"no answer within {CLI_TIMEOUT_S} s"
    return process.returncode or 0, out.decode(), err.decode()


def claude_code(model: str | None, run: CliRunner = _run_cli) -> Complete:
    """Claude Code in print mode with every tool off; billed to the human's own plan, so no
    per-call cost is recorded."""
    argv = ["claude", "-p", "--output-format", "text", "--tools", ""]
    argv += ["--strict-mcp-config", "--no-session-persistence"]
    argv += ["--system-prompt", prompts.load("evals/judge_system")]
    if model:
        argv += ["--model", model]

    async def complete(prompt: str) -> Completion:
        code, out, err = await run(argv, prompt)
        if code != 0:
            raise JudgeError(f"claude exited with {code}: {err.strip()[-500:]}")
        return Completion(out.strip())

    return complete


def build_judge(spec: str, models: ModelSettings) -> Judge:
    """The judge `spec` names. The OpenRouter key comes from `models` or the environment."""
    if spec == MANUAL:
        return ManualJudge()
    if spec == CLAUDE_CODE or spec.startswith(f"{CLAUDE_CODE}:"):
        if shutil.which("claude") is None:
            raise JudgeError("EVAL_JUDGE_MODEL=claude-code needs the claude CLI on PATH")
        return RootCauseJudge(spec, claude_code(spec.partition(":")[2] or None))
    key = models.openrouter_api_key.get_secret_value() if models.openrouter_api_key else None
    key = key or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise JudgeError(f"the judge {spec!r} runs on OpenRouter: set OPENROUTER_API_KEY")
    return RootCauseJudge(spec, openrouter(spec, key))


@cache
def _adapter() -> type:
    """DeepEval's model interface around a `Complete`, built on first use so that Bleep's tests
    and the harness's exact checks run without DeepEval installed."""
    from deepeval.models import DeepEvalBaseLLM

    class CompleteModel(DeepEvalBaseLLM):
        def __init__(self, name: str, complete: Complete) -> None:
            self._complete = complete
            self.usd: float | None = None
            super().__init__(name)

        def load_model(self, *args: Any, **kwargs: Any) -> CompleteModel:
            return self

        def get_model_name(self, *args: Any, **kwargs: Any) -> str:
            return self.name

        async def a_generate(self, prompt: str, *args: Any, **kwargs: Any) -> str:
            # GEval passes a `schema`; the prompt asks for the same JSON, which GEval parses.
            done = await self._complete(prompt)
            if done.usd is not None:
                self.usd = (self.usd or 0.0) + done.usd
            return done.text

        def generate(self, prompt: str, *args: Any, **kwargs: Any) -> str:
            return asyncio.run(self.a_generate(prompt))

    return CompleteModel


class RootCauseJudge:
    """`judge` makes one model call per attempt: the evaluation steps are fixed, so GEval does not
    ask the model to write them. An answer GEval cannot read (a judge that leaves out the score) or
    a failed call is asked for again, up to JUDGE_ATTEMPTS times in all."""

    def __init__(self, name: str, complete: Complete) -> None:
        self.name = name
        self._complete = complete

    async def judge(self, expected: str, diagnosis: Diagnosis) -> Judgement:
        usd: float | None = None
        last_error = "no attempt was made"
        for attempt in range(1, JUDGE_ATTEMPTS + 1):
            try:
                score, reason, spent = await self._attempt(expected, diagnosis)
            except _AttemptFailed as failed:
                last_error = str(failed)
                usd = _add(usd, failed.usd)
                continue
            return Judgement(
                score=score,
                passed=score >= PASS_SCORE,
                reason=reason,
                usd=_add(usd, spent),
                attempts=attempt,
            )
        raise JudgeError(f"{last_error} (after {JUDGE_ATTEMPTS} attempts)", JUDGE_ATTEMPTS, usd)

    async def _attempt(
        self, expected: str, diagnosis: Diagnosis
    ) -> tuple[float, str, float | None]:
        """One GEval run on a fresh metric and model, so nothing half-parsed carries over."""
        from deepeval.metrics import GEval
        from deepeval.test_case import LLMTestCase, SingleTurnParams

        model = _adapter()(self.name, self._complete)
        metric = GEval(
            name="Root cause",
            evaluation_params=[SingleTurnParams.ACTUAL_OUTPUT, SingleTurnParams.EXPECTED_OUTPUT],
            evaluation_steps=evaluation_steps(),
            model=model,
            threshold=PASS_SCORE,
            async_mode=True,
        )
        case = LLMTestCase(input="", actual_output=describe(diagnosis), expected_output=expected)
        try:
            await metric.a_measure(case, _show_indicator=False)
        except JudgeError as error:
            raise _AttemptFailed(str(error), model.usd) from error
        except Exception as error:  # an unparsable answer, a provider error
            raise _AttemptFailed(f"{type(error).__name__}: {error}", model.usd) from error
        return float(metric.score or 0.0), metric.reason or "", model.usd


class _AttemptFailed(Exception):
    def __init__(self, message: str, usd: float | None) -> None:
        super().__init__(message)
        self.usd = usd


def _add(total: float | None, more: float | None) -> float | None:
    if more is None:
        return total
    return (total or 0.0) + more
