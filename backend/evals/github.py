"""A GitHub for the evals: a fixture's commits served to Bleep's own client, so the GitHub tools
run unchanged and nothing reaches github.com.

A fixture is a JSON file in `evals/fixtures/github/`: commits, newest first, each with its age in
minutes at the start of the run, its author, message and files. `fixture_client` answers the two
requests the tools make (the commit list, with `since`, `path` and `per_page`, and one commit by
a sha or its prefix) in GitHub's shape, for whatever repo the service names, since the fixture
stands in for that repo. Anything else is GitHub's 404.
"""

import hashlib
import json
import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bleep.hosts.github import GithubClient

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "github"
NOT_FOUND = {"message": "Not Found", "status": "404"}
# A word of 7 to 40 hex characters, as a sha is written in a text, not inside a name, a path or a
# value; the same edges as the report's check (`bleep.graph.commits`).
_HEX_WORD = re.compile(r"(?<![\w./:@#=-])[0-9a-f]{7,40}(?![\w/:@-])(?!\.\w)", re.IGNORECASE)


class GithubFixtureError(ValueError):
    """A GitHub fixture is missing or malformed."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FixtureFile(_Strict):
    filename: str
    status: str = "modified"
    additions: int = Field(0, ge=0)
    deletions: int = Field(0, ge=0)
    patch: str | None = None


class FixtureCommit(_Strict):
    sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    minutes_ago: int = Field(ge=0)
    author: str
    message: str = Field(min_length=1)
    files: list[FixtureFile] = Field(default_factory=list)

    def touches(self, path: str) -> bool:
        return any(
            f.filename == path or f.filename.startswith(path.rstrip("/") + "/") for f in self.files
        )


class GithubFixture(_Strict):
    commits: list[FixtureCommit]  # newest first

    def with_newest(self, commit: FixtureCommit) -> GithubFixture:
        commits = sorted([commit, *self.commits], key=lambda c: c.minutes_ago)
        return GithubFixture(commits=commits)


def load_github_fixture(name: str, directory: Path = FIXTURES_DIR) -> GithubFixture:
    path = directory / f"{name}.json"
    try:
        return GithubFixture.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, ValidationError) as error:
        raise GithubFixtureError(f"GitHub fixture {path}: {error}") from error


def named_commits(fixture: GithubFixture, texts: Iterable[str]) -> list[str]:
    """The short shas of the fixture's commits that `texts` name by sha, in the fixture's order:
    a hex word of 7 characters or more, in any case, that starts the commit's sha (#350)."""
    words = {word.lower() for text in texts for word in _HEX_WORD.findall(text)}
    return [c.sha[:7] for c in fixture.commits if any(c.sha.startswith(w) for w in words)]


def planted_commit(
    key: str, message: str, files: list[FixtureFile], minutes_ago: int
) -> FixtureCommit:
    """A commit carrying `message`, with a sha that is stable for `key`."""
    sha = hashlib.sha1(key.encode()).hexdigest()
    return FixtureCommit(
        sha=sha, minutes_ago=minutes_ago, author="shop-dev", message=message, files=files
    )


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _shape(repo: str, commit: FixtureCommit, at: datetime, files: bool) -> dict:
    person = {"name": commit.author, "date": _iso(at)}
    shaped: dict = {
        "sha": commit.sha,
        "commit": {"author": person, "committer": person, "message": commit.message},
        "author": {"login": commit.author},
        "html_url": f"https://github.com/{repo}/commit/{commit.sha}",
    }
    if files:
        shaped["files"] = [f.model_dump(exclude_none=True) for f in commit.files]
        shaped["stats"] = {
            "additions": sum(f.additions for f in commit.files),
            "deletions": sum(f.deletions for f in commit.files),
        }
    return shaped


def fixture_client(
    fixture: GithubFixture, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
) -> GithubClient:
    """A client whose GitHub is `fixture`, dated from `clock()` now (the start of the run)."""
    start = clock()
    dated = [(c, start - timedelta(minutes=c.minutes_ago)) for c in fixture.commits]

    def handler(request: httpx.Request) -> httpx.Response:
        parts = request.url.path.strip("/").split("/")
        if len(parts) < 4 or parts[0] != "repos" or parts[3] != "commits" or len(parts) > 5:
            return httpx.Response(404, json=NOT_FOUND)
        repo = f"{parts[1]}/{parts[2]}"
        if len(parts) == 5:
            found = next(((c, at) for c, at in dated if c.sha.startswith(parts[4].lower())), None)
            if found is None:
                return httpx.Response(422, json={"message": f"No commit found for SHA: {parts[4]}"})
            return httpx.Response(200, json=_shape(repo, *found, files=True))
        params = request.url.params
        since = datetime.fromisoformat(params["since"]) if "since" in params else None
        path = params.get("path")
        listed = [
            _shape(repo, c, at, files=False)
            for c, at in dated
            if (since is None or at >= since) and (not path or c.touches(path))
        ]
        return httpx.Response(200, json=listed[: int(params.get("per_page", "30"))])

    return GithubClient(None, transport=httpx.MockTransport(handler))
