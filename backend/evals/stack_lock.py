"""One eval at a time on the demo stack, across every checkout and worktree on this machine.

The evals that reset the shop or switch its faults (`evals.run`, `evals.injection`) hold an
exclusive `flock` on one file at a fixed path outside the checkout for their whole round, so a
second one refuses to start instead of resetting the shop under the first (#486). The kernel
drops the lock when the holder exits, even on Ctrl-C or `kill -9`, so there is never a stale
holder to clean up. The file keeps one line on who holds it, for the refusal to name.
"""

import contextlib
import fcntl
import json
import os
import shlex
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_STACK_LOCK = Path.home() / ".cache" / "bleep" / "demo-stack.lock"


class StackBusy(Exception):
    """Another eval holds the demo stack."""


def eval_command(module: str, argv: Sequence[str]) -> str:
    """The command line a holder is named by: `python -m <module>` and its arguments."""
    return shlex.join(["python", "-m", module, *argv])


@contextlib.contextmanager
def holding_demo_stack(path: Path, command: str) -> Iterator[None]:
    """Hold the demo stack until the block ends; raises `StackBusy` at once if another eval
    holds it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Opened without truncating: the holder's line must survive a refused attempt. The file is
    # never removed, so every eval locks the same inode.
    with open(os.open(path, os.O_RDWR | os.O_CREAT, 0o644), "r+", encoding="utf-8") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StackBusy(_busy_message(path, file.read())) from None
        file.truncate(0)
        file.write(_holder(command) + "\n")
        file.flush()
        try:
            yield
        finally:
            file.seek(0)
            file.truncate(0)


def _holder(command: str) -> str:
    return json.dumps(
        {
            "pid": os.getpid(),
            "since": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "cwd": os.getcwd(),
            "command": command,
        }
    )


def _busy_message(path: Path, line: str) -> str:
    try:
        holder = json.loads(line)
        who = (
            f"pid {holder['pid']}, since {holder['since']}: {holder['command']} "
            f"(in {holder['cwd']})"
        )
    except ValueError, TypeError, KeyError:
        who = "an eval that has not named itself yet"
    return (
        f"the demo stack is in use by another eval, {who}. Wait until it finishes, then start "
        f"this one again (lock file: {path})"
    )
