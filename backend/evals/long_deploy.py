"""Builds `fixtures/github/bad-deploy-long.json`, the GitHub fixture of the `bad-deploy-long`
scenario (#497): the bad-deploy fault with a deploy 41 days old that brought 300 commits.

It takes the commits of `bad-deploy-old.json` (the 24 pushed since the deploy, the deploy's own
commit, the commit that reads `price_cents` and the one deployed before) and puts 298 commits that
touch nothing of the shop between the deploy's commit and the cause, so the cause is the oldest of
the deploy: more than 250 commits from the newest, past what GitHub's unpaged compare lists, and
older than the widest window (720 hours) `recent_commits` reads. `deployed_commits` reaches it only
by following the `skip` its cut note names. The fillers are generated from fixed word lists, so the
file can be rebuilt; a test checks that the committed file is what this builds.

    uv run python -m evals.long_deploy        # in backend/, rewrites the fixture
"""

import hashlib
import json
from pathlib import Path

from evals.github import FIXTURES_DIR

SOURCE = "bad-deploy-old"
TARGET = "bad-deploy-long"
FILLERS = 298  # between the deploy's commit and the cause: the deploy brings 298 + 2 commits
DEPLOY_COMMIT = "d9f9be16d2f9d5fd043579e64975986727895711"
CAUSE_COMMIT = "335006387524dfc3b1f9a939d3bb0ca6be90b5c2"
PREVIOUS_COMMIT = "a2696ac3d02b358c7074eea32e2063e38e6ddfd6"
DEPLOY_AGE_MINUTES = 41 * 24 * 60  # the deploy went live 41 days ago
FIRST_FILLER_MINUTES = DEPLOY_AGE_MINUTES + 160  # the deploy's commit sits 60 minutes before it
FILLER_STEP_MINUTES = 100

VERBS = [
    "Fix",
    "Tidy",
    "Document",
    "Test",
    "Rename",
    "Bump",
    "Simplify",
    "Clarify",
    "Log",
    "Speed up",
    "Reword",
    "Cover",
]
THINGS: list[tuple[str, str]] = [
    ("the setup guide", "docs/setup.md"),
    ("the architecture page", "docs/architecture.md"),
    ("the hosts file loader", "backend/src/bleep/hosts/config.py"),
    ("the event cursor", "backend/src/bleep/watcher/events.py"),
    ("the incident timeline", "frontend/src/components/timeline.tsx"),
    ("the settings page", "frontend/src/app/settings/page.tsx"),
    ("the knowledge index", "backend/src/bleep/knowledge/index.py"),
    ("the approval message", "backend/src/bleep/prompts/notify/approval.md"),
    ("the SSH reconnect", "backend/src/bleep/hosts/ssh.py"),
    ("the chat panel", "frontend/src/components/chat.tsx"),
    ("the CI workflow", ".github/workflows/ci.yml"),
    ("the demo script", "docs/demo.md"),
    ("the README", "README.md"),
    ("the plugin list", "frontend/src/components/plugins.tsx"),
    ("the report writer", "backend/src/bleep/graph/report.py"),
    ("the Telegram formatter", "backend/src/bleep/notify/format.py"),
    ("the memory store", "backend/src/bleep/memory/store.py"),
    ("the URL checker", "backend/src/bleep/watcher/url_check.py"),
    ("the overview card", "frontend/src/app/page.tsx"),
    ("the dev scripts", "scripts/board.sh"),
]


def _filler(i: int) -> dict:
    verb = VERBS[(i + i // len(THINGS)) % len(VERBS)]
    thing, path = THINGS[i % len(THINGS)]
    return {
        "sha": hashlib.sha1(f"{TARGET}-{i}".encode()).hexdigest(),
        "minutes_ago": FIRST_FILLER_MINUTES + i * FILLER_STEP_MINUTES,
        "author": "bleep-dev",
        "message": f"{verb} {thing}",
        "files": [
            {
                "filename": path,
                "additions": 1,
                "deletions": 1,
                "patch": "@@ -1 +1 @@\n-old line\n+new line",
            }
        ],
    }


def build() -> dict:
    """The fixture: commits newest first, with the deploy and its previous commit."""
    source = json.loads((FIXTURES_DIR / f"{SOURCE}.json").read_text(encoding="utf-8"))
    commits = {c["sha"]: c for c in source["commits"]}
    deployed_at = commits[DEPLOY_COMMIT]["minutes_ago"]
    since = [c for c in source["commits"] if c["minutes_ago"] < deployed_at]
    fillers = [_filler(i) for i in range(FILLERS)]
    oldest = fillers[-1]["minutes_ago"] + FILLER_STEP_MINUTES
    cause = commits[CAUSE_COMMIT] | {"minutes_ago": oldest}
    previous = commits[PREVIOUS_COMMIT] | {"minutes_ago": oldest + 1000}
    deploy = commits[DEPLOY_COMMIT] | {"minutes_ago": DEPLOY_AGE_MINUTES + 60}
    return {
        "commits": [*since, deploy, *fillers, cause, previous],
        "deploy": {
            "commit": DEPLOY_COMMIT,
            "previous": PREVIOUS_COMMIT,
            "minutes_ago": DEPLOY_AGE_MINUTES,
        },
    }


def render() -> str:
    return json.dumps(build(), indent=1) + "\n"


if __name__ == "__main__":
    path = Path(FIXTURES_DIR) / f"{TARGET}.json"
    path.write_text(render(), encoding="utf-8")
    print(f"wrote {path}")
