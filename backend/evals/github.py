"""A GitHub for the evals: a fixture's commits served to Bleep's own client, so the GitHub tools
run unchanged and nothing reaches github.com.

A fixture is a JSON file in `evals/fixtures/github/`: commits, newest first, each with its age in
minutes at the start of the run, its author, message and files, on one line of history. An
optional `deploy` names the commit that runs, the one deployed before it and how long ago it went
live; `FixtureDeploys` hands it to `deployed_commits` as the Coolify plugin's deployments would,
for whatever service asks, since the demo shop runs on Compose. `fixture_client` answers the
requests the tools make (the commit list, with `since`, `path`, `per_page` and `sha`, one commit
by a sha or its prefix, and the compare of two) in GitHub's shape, for whatever repo the service
names, since the fixture stands in for that repo. Anything else is GitHub's 404.
"""

import hashlib
import json
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Self

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from bleep.graph.commits import HEX_WORD
from bleep.hosts.github import GithubClient
from bleep.schemas.github import RunningCommit
from bleep.schemas.services import Service

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "github"
NOT_FOUND = {"message": "Not Found", "status": "404"}


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


class FixtureDeploy(_Strict):
    """The deploy that runs: its commit, the commit deployed before it, and since when."""

    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    previous: str | None = Field(None, pattern=r"^[0-9a-f]{40}$")
    minutes_ago: int = Field(ge=0)


class GithubFixture(_Strict):
    commits: list[FixtureCommit]  # newest first
    deploy: FixtureDeploy | None = None

    @model_validator(mode="after")
    def _deploys_its_own_commits(self) -> Self:
        named = {self.deploy.commit, self.deploy.previous} - {None} if self.deploy else set()
        if not named <= {c.sha for c in self.commits}:
            raise ValueError("the deploy names a commit the fixture does not have")
        return self

    def with_newest(self, commit: FixtureCommit) -> GithubFixture:
        commits = sorted([commit, *self.commits], key=lambda c: c.minutes_ago)
        return GithubFixture(commits=commits, deploy=self.deploy)


class FixtureDeploys:
    """The fixture's deploy as the commit every service runs, live since `start` minus its age;
    None without one, as for a service no deploy tool deployed."""

    def __init__(self, fixture: GithubFixture, start: datetime) -> None:
        self._deploy = fixture.deploy
        self._start = start

    async def running_commit(self, service: Service) -> RunningCommit | None:
        if self._deploy is None:
            return None
        return RunningCommit(
            sha=self._deploy.commit,
            how="coolify",
            since=self._start - timedelta(minutes=self._deploy.minutes_ago),
            previous=self._deploy.previous,
        )


def load_github_fixture(name: str, directory: Path = FIXTURES_DIR) -> GithubFixture:
    path = directory / f"{name}.json"
    try:
        return GithubFixture.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, ValidationError) as error:
        raise GithubFixtureError(f"GitHub fixture {path}: {error}") from error


def named_commits(fixture: GithubFixture, texts: Iterable[str]) -> list[str]:
    """The short shas of the fixture's commits that `texts` name by sha, in the fixture's order:
    a hex word of 7 characters or more, read as the check of what the model writes reads one
    (`bleep.graph.commits.HEX_WORD`), in any case, that starts the commit's sha (#350). Any such
    word counts here, mixed or not, since only a fixture commit's sha can match."""
    words = {word.lower() for text in texts for word in HEX_WORD.findall(text)}
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

    def index(sha: str) -> int | None:
        return next((i for i, (c, _) in enumerate(dated) if c.sha.startswith(sha.lower())), None)

    def compare(repo: str, basehead: str) -> httpx.Response:
        base, _, head = basehead.partition("...")
        b, h = index(base), index(head)
        if b is None or h is None:
            return httpx.Response(404, json=NOT_FOUND)
        between = dated[h:b] if b > h else []  # newest first; GitHub lists them oldest first
        status = "ahead" if b > h else "behind" if b < h else "identical"
        listed = [_shape(repo, c, at, files=False) for c, at in reversed(between)]
        return httpx.Response(
            200,
            json={
                "status": status,
                "ahead_by": len(between),
                "behind_by": max(0, h - b),
                "total_commits": len(between),
                "commits": listed,
            },
        )

    def handler(request: httpx.Request) -> httpx.Response:
        parts = request.url.path.strip("/").split("/")
        if len(parts) == 5 and parts[0] == "repos" and parts[3] == "compare":
            return compare(f"{parts[1]}/{parts[2]}", parts[4])
        if len(parts) < 4 or parts[0] != "repos" or parts[3] != "commits" or len(parts) > 5:
            return httpx.Response(404, json=NOT_FOUND)
        repo = f"{parts[1]}/{parts[2]}"
        if len(parts) == 5:
            found = next(((c, at) for c, at in dated if c.sha.startswith(parts[4].lower())), None)
            if found is None:
                return httpx.Response(422, json={"message": f"No commit found for SHA: {parts[4]}"})
            return httpx.Response(200, json=_shape(repo, *found, files=True))
        params = request.url.params
        first = index(params["sha"]) if "sha" in params else 0
        if first is None:
            return httpx.Response(404, json=NOT_FOUND)
        since = datetime.fromisoformat(params["since"]) if "since" in params else None
        path = params.get("path")
        listed = [
            _shape(repo, c, at, files=False)
            for c, at in dated[first:]
            if (since is None or at >= since) and (not path or c.touches(path))
        ]
        return httpx.Response(200, json=listed[: int(params.get("per_page", "30"))])

    return GithubClient(None, transport=httpx.MockTransport(handler))
