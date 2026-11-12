import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bleep.schemas.settings import ModelSettings
from evals import injection, run
from evals.run import EvalSettings
from evals.stack_lock import StackBusy, eval_command, holding_demo_stack

HOSTS = """\
hosts:
  - id: demo
    kind: socket
"""


def holder_of(lock: Path) -> dict:
    return json.loads(lock.read_text(encoding="utf-8"))


def refusal(lock: Path, command: str) -> str:
    """The message an eval that wants the stack at `lock` is refused with."""
    with pytest.raises(StackBusy) as refused, holding_demo_stack(lock, command):
        pytest.fail("the eval got the stack")
    return str(refused.value)


def test_a_second_eval_is_refused_while_the_first_holds_the_stack(tmp_path):
    lock = tmp_path / "stack.lock"
    with holding_demo_stack(lock, "python -m evals.run --only hung"):
        message = refusal(lock, "python -m evals.run --only bad-deploy")
        assert f"pid {os.getpid()}" in message
        assert "python -m evals.run --only hung" in message
        assert str(lock) in message


def test_a_refused_attempt_leaves_the_holders_line(tmp_path):
    lock = tmp_path / "stack.lock"
    with holding_demo_stack(lock, "python -m evals.run --only hung"):
        refusal(lock, "python -m evals.injection")
        assert holder_of(lock)["command"] == "python -m evals.run --only hung"


def test_the_stack_is_free_again_when_the_holder_finishes_or_fails(tmp_path):
    lock = tmp_path / "stack.lock"
    with holding_demo_stack(lock, "first"):
        pass
    with pytest.raises(RuntimeError), holding_demo_stack(lock, "second"):
        raise RuntimeError("the run broke")
    with holding_demo_stack(lock, "third"):
        assert holder_of(lock)["command"] == "third"
    assert lock.read_text(encoding="utf-8") == ""


def test_a_folder_that_does_not_exist_yet_is_made(tmp_path):
    lock = tmp_path / "cache" / "bleep" / "stack.lock"
    with holding_demo_stack(lock, "python -m evals.run"):
        assert lock.is_file()


def test_another_process_holding_the_stack_refuses_this_one_until_it_exits(tmp_path):
    lock = tmp_path / "stack.lock"
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLD_UNTIL_STDIN_CLOSES, str(lock)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "holding"
        assert f"pid {holder.pid}" in refusal(lock, "python -m evals.run")
    finally:
        assert holder.stdin is not None
        holder.stdin.close()
        holder.wait(timeout=10)
    with holding_demo_stack(lock, "python -m evals.run"):
        pass


HOLD_UNTIL_STDIN_CLOSES = """\
import sys
from pathlib import Path
from evals.stack_lock import holding_demo_stack
with holding_demo_stack(Path(sys.argv[1]), "python -m evals.injection --repeat 3"):
    print("holding", flush=True)
    sys.stdin.read()
"""


def test_a_holder_that_has_not_named_itself_yet_is_still_refused(tmp_path):
    lock = tmp_path / "stack.lock"
    with holding_demo_stack(lock, "python -m evals.run"):
        lock.write_text("", encoding="utf-8")
        assert "has not named itself yet" in refusal(lock, "python -m evals.injection")


def test_the_command_names_the_module_and_its_arguments():
    assert eval_command("evals.run", ["--only", "hung", "--repeat", "3"]) == (
        "python -m evals.run --only hung --repeat 3"
    )


def test_the_settings_lock_a_file_outside_any_checkout(monkeypatch):
    monkeypatch.delenv("EVAL_STACK_LOCK")
    default = EvalSettings(_env_file=None).stack_lock  # type: ignore[call-arg]
    assert not default.is_relative_to(run.REPO)
    assert default.is_relative_to(Path.home())


def eval_settings(tmp_path: Path) -> EvalSettings:
    hosts = tmp_path / "hosts.yaml"
    hosts.write_text(HOSTS, encoding="utf-8")
    script = tmp_path / "fault.sh"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    return EvalSettings(  # type: ignore[call-arg]
        _env_file=None,
        hosts_file=hosts,
        docker_endpoint="unix:///fake.sock",
        fault_script=script,
        judge_model="manual",
        stack_lock=tmp_path / "stack.lock",
        models=ModelSettings(provider="local", local_model="tiny-local"),
    )


@pytest.mark.parametrize(
    ("runner", "argv"),
    [
        (run, ["--only", "hung", "--no-reset"]),
        (injection, ["--only", "log-base64", "--no-reset"]),
    ],
)
def test_an_eval_that_drives_the_shop_does_not_start_while_another_holds_it(
    tmp_path, monkeypatch, capsys, runner, argv
):
    started = []

    async def fake_run(*args):
        started.append(args)
        return 0

    monkeypatch.setattr(runner, "_run", fake_run)
    settings = eval_settings(tmp_path)

    with holding_demo_stack(settings.stack_lock, "python -m evals.run --only hung"):
        assert runner.main(argv, settings=settings) == 2
    assert started == []
    assert "in use by another eval" in capsys.readouterr().err

    assert runner.main(argv, settings=settings) == 0
    assert len(started) == 1


@pytest.mark.parametrize("runner", [run, injection])
def test_a_running_eval_holds_the_stack(tmp_path, monkeypatch, runner):
    settings = eval_settings(tmp_path)
    seen = []

    async def fake_run(*args):
        seen.append(holder_of(settings.stack_lock)["command"])
        return 0

    monkeypatch.setattr(runner, "_run", fake_run)
    argv = ["--only", "hung" if runner is run else "log-base64"]

    assert runner.main(argv, settings=settings) == 0
    assert seen == [f"python -m {runner.__name__} {' '.join(argv)}"]


def test_rendering_injection_results_needs_no_stack(tmp_path, capsys):
    raw = tmp_path / "round.jsonl"
    raw.write_text("", encoding="utf-8")
    with holding_demo_stack(EvalSettings().stack_lock, "python -m evals.run --only hung"):
        assert injection.main(["--label", "Round 1", "--render", str(raw)]) == 0
