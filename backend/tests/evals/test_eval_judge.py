"""The root cause judge: never the model under test, and its two backends, with no model."""

import json

import pytest
from pydantic import SecretStr

from bleep.schemas.graph import Diagnosis, Evidence
from bleep.schemas.settings import ModelSettings
from evals.judge import (
    Completion,
    JudgeError,
    RootCauseJudge,
    SameModel,
    build_judge,
    check_distinct,
    claude_code,
    describe,
    evaluation_steps,
    model_under_test,
)

LOCAL = ModelSettings(provider="local", local_model="huihui-qwen3.8-27b-abliterated")
CLOUD = ModelSettings(provider="openrouter", model="openai/gpt-6-luna")
DIAGNOSIS = Diagnosis(
    root_cause="shop-db is not running, so shop-web cannot reach its database",
    category="dependency",
    evidence=[Evidence(source="gather", detail="shop-db exited 0 at 12:01")],
    confidence=0.8,
)


def test_the_model_under_test_is_the_one_the_provider_uses():
    assert model_under_test(LOCAL) == "huihui-qwen3.8-27b-abliterated"
    assert model_under_test(CLOUD) == "openai/gpt-6-luna"


@pytest.mark.parametrize(
    ("judge", "tested"),
    [
        ("openai/gpt-6-luna", "openai/gpt-6-luna"),
        ("openai/gpt-6-luna", "GPT-6-Luna"),
        ("qwen/huihui-qwen3.8-27b-abliterated", "huihui-qwen3.8-27b-abliterated"),
        ("claude-code:claude-opus-5-5", "anthropic/claude-opus-5-5"),
    ],
)
def test_the_judge_may_not_be_the_model_under_test(judge, tested):
    with pytest.raises(SameModel, match="judge"):
        check_distinct(judge, tested)


def test_a_different_judge_is_allowed():
    check_distinct("openai/gpt-6-luna", "huihui-qwen3.8-27b-abliterated")
    check_distinct("claude-code", "huihui-qwen3.8-27b-abliterated")


def test_an_openrouter_judge_needs_a_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(JudgeError, match="OPENROUTER_API_KEY"):
        build_judge("openai/gpt-6-luna", LOCAL)
    keyed = LOCAL.model_copy(update={"openrouter_api_key": SecretStr("k")})
    assert build_judge("openai/gpt-6-luna", keyed).name == "openai/gpt-6-luna"


def test_claude_code_is_a_judge_without_a_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("evals.judge.shutil.which", lambda _: "/usr/bin/claude")
    assert build_judge("claude-code:opus", LOCAL).name == "claude-code:opus"


def test_claude_code_judge_is_refused_without_the_cli(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("evals.judge.shutil.which", lambda _: None)
    with pytest.raises(JudgeError, match="claude CLI"):
        build_judge("claude-code:opus", LOCAL)


class FakeCli:
    def __init__(self, stdout: str = "ok", code: int = 0) -> None:
        self.stdout, self.code = stdout, code
        self.calls: list[tuple[list[str], str]] = []

    async def __call__(self, argv: list[str], stdin: str) -> tuple[int, str, str]:
        self.calls.append((argv, stdin))
        return self.code, self.stdout, "it broke"


async def test_claude_code_gets_the_prompt_on_stdin_with_no_tools_and_the_model_asked_for():
    cli = FakeCli(stdout='{"score": 9}\n')
    done = await claude_code("opus", cli)("judge this")
    assert done == Completion(text='{"score": 9}', usd=None)
    argv, stdin = cli.calls[0]
    assert stdin == "judge this"
    assert argv[:2] == ["claude", "-p"]
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--model") + 1] == "opus"
    assert "--no-session-persistence" in argv and "--strict-mcp-config" in argv


async def test_claude_code_without_a_model_uses_the_cli_default():
    cli = FakeCli()
    await claude_code(None, cli)("judge this")
    assert "--model" not in cli.calls[0][0]


async def test_a_failing_claude_code_call_is_a_judge_error():
    with pytest.raises(JudgeError, match="it broke"):
        await claude_code(None, FakeCli(code=1))("judge this")


def test_the_evaluation_steps_come_from_the_prompt_file():
    steps = evaluation_steps()
    assert len(steps) >= 2
    assert all(step and not step.startswith("-") for step in steps)


def test_the_judge_reads_the_cause_the_category_and_the_evidence():
    text = describe(DIAGNOSIS)
    assert DIAGNOSIS.root_cause in text
    assert "dependency" in text
    assert "shop-db exited 0 at 12:01" in text


async def test_geval_turns_the_judges_answer_into_a_score_and_a_pass():
    pytest.importorskip("deepeval")
    prompts: list[str] = []

    async def complete(prompt: str) -> Completion:
        prompts.append(prompt)
        return Completion(json.dumps({"score": 9, "reason": "names the stopped db"}), usd=0.002)

    judgement = await RootCauseJudge("fake-judge", complete).judge(
        "The database container shop-db is stopped.", DIAGNOSIS
    )
    assert len(prompts) == 1  # the steps are given, so GEval asks only for the score
    assert "The database container shop-db is stopped." in prompts[0]
    assert DIAGNOSIS.root_cause in prompts[0]
    assert judgement.score == pytest.approx(0.9)
    assert judgement.passed is True  # 0.9 is above PASS_SCORE
    assert judgement.reason == "names the stopped db"
    assert judgement.usd == pytest.approx(0.002)


async def test_a_low_score_fails():
    pytest.importorskip("deepeval")

    async def complete(prompt: str) -> Completion:
        return Completion(json.dumps({"score": 2, "reason": "blames the network"}))

    judgement = await RootCauseJudge("fake-judge", complete).judge("db stopped", DIAGNOSIS)
    assert judgement.score == pytest.approx(0.2)
    assert judgement.passed is False
    assert judgement.usd is None
