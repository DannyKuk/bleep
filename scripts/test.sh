#!/usr/bin/env bash
# The backend suite: every test, in parallel, then the wall-clock tests alone.
# Arguments go to both runs, e.g. scripts/test.sh tests/guard or scripts/test.sh -x.
set -euo pipefail

# One run at a time on this machine, across every checkout and worktree: a second run's parallel
# pass would load the cores under the first one's timing tests. The script runs itself again
# under the lock (lockf on macOS, flock on Linux); the kernel drops it when the run ends.
lock="${XDG_CACHE_HOME:-$HOME/.cache}/bleep/tests.lock"
if [[ -z ${BLEEP_TESTS_LOCKED:-} ]]; then
  mkdir -p "$(dirname "$lock")"
  export BLEEP_TESTS_LOCKED=1
  if command -v lockf >/dev/null; then
    lockf -s -k -t 0 "$lock" true || echo "Another test run holds $lock; waiting for it." >&2
    exec lockf -s -k "$lock" "$0" "$@"
  elif command -v flock >/dev/null; then
    flock -n "$lock" true || echo "Another test run holds $lock; waiting for it." >&2
    exec flock "$lock" "$0" "$@"
  fi
fi

cd "$(git rev-parse --show-toplevel)/backend"

# A test marked `timing` asserts an absolute bar, such as the event loop staying free within
# 0.3 s; with every core busy it would fail for the load, not the code, so it runs afterwards.
status=0
run() {
  local code=0
  uv run pytest "$@" || code=$?
  # 5 means the run selected no tests, e.g. an area with no timing tests
  if [[ $code -ne 0 && $code -ne 5 ]]; then status=$code; fi
}
run -n auto -m "not timing" "$@"
run -m timing "$@"
exit "$status"
