"""A small read-only client for the GitHub REST API: a repo's recent commits, one commit with its
diff, and the repo's README and docs files.

The GitHub tools read commits through it, and the knowledge ingest reads READMEs and docs. It
lives with the hosts file because the token and each service's `repo` are set there, and so that
`tools` and `knowledge` can both use it. Without a token it reads public repos (GitHub allows 60
requests an hour per IP); a token raises that and opens private repos. The token goes only into
the Authorization header and is never logged. Every failure is a `GithubError`.
"""

import base64
import binascii
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, SecretStr, TypeAdapter, ValidationError

from bleep.hosts.config import REPO_NAME, GithubConfig, SecretMissing, resolve_secret
from bleep.schemas.github import Commit, CommitDetail, FileChange, RepoEntry, RepoFile

log = logging.getLogger(__name__)

API = "https://api.github.com"
API_VERSION = "2022-11-28"
TIMEOUT_S = 10.0
MAX_PER_PAGE = 100
# Folders `list_files` opens, one request each, so a deep tree cannot spend the rate limit.
MAX_FOLDERS = 30
MAX_PATH = 300
_SHA = re.compile(r"[0-9a-fA-F]{7,40}")


class GithubError(Exception):
    """GitHub did not give what was asked for. The message is Bleep's own, never GitHub's."""


class NotFound(GithubError):
    """No such repo, commit or file (`kind`); a private repo without a token looks like no repo."""

    def __init__(
        self, message: str, kind: Literal["repo", "commit", "file"], status: int = 404
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status


class RateLimited(GithubError):
    def __init__(self, message: str, reset_at: datetime | None = None) -> None:
        super().__init__(message)
        self.reset_at = reset_at


class GithubUnavailable(GithubError):
    """GitHub could not be reached, timed out or answered with a server error."""


class TooLarge(GithubError):
    """The file is over the 1 MB the contents API serves, so it comes without its text."""


class _Person(BaseModel):
    name: str = ""
    date: datetime


class _GitCommit(BaseModel):
    author: _Person
    committer: _Person
    message: str


class _User(BaseModel):
    login: str


class _Stats(BaseModel):
    additions: int = 0
    deletions: int = 0


class _RawCommit(BaseModel):
    sha: str
    commit: _GitCommit
    author: _User | None = None
    html_url: str | None = None
    stats: _Stats | None = None
    files: list[FileChange] = []

    def summary(self) -> Commit:
        return Commit(
            sha=self.sha,
            date=self.commit.committer.date,
            author=self.author.login if self.author else self.commit.author.name or "unknown",
            message=self.commit.message,
            url=self.html_url,
        )


class _RawEntry(BaseModel):
    path: str
    type: str
    size: int = 0
    html_url: str | None = None


class _RawFile(_RawEntry):
    sha: str
    encoding: str | None = None
    content: str | None = None


def github_token(config: GithubConfig | None) -> SecretStr | None:
    """The token the hosts file points at, or None: no `github` section, an unset variable or an
    empty one all mean reading public repos without a token."""
    if config is None:
        return None
    try:
        token = resolve_secret(config.token)
    except SecretMissing as missing:
        log.warning("github: %s; reading public repos without a token", missing)
        return None
    return token if token.get_secret_value() else None


def check_repo(repo: str) -> None:
    if not REPO_NAME.fullmatch(repo):
        raise GithubError(f"{repo!r} is not a GitHub repo name (owner/name)")


def check_sha(sha: str) -> None:
    if not _SHA.fullmatch(sha):
        raise GithubError("a commit sha is 7 to 40 hexadecimal characters")


def check_path(path: str) -> None:
    """A repo-relative path: no leading slash, no empty, `.` or `..` part, no backslash or
    control character. Spaces, `#`, `%` and `?` are fine: the path is URL-quoted."""
    parts = path.split("/")
    if (
        not path
        or len(path) > MAX_PATH
        or any(p in ("", ".", "..") for p in parts)
        or re.search(r"[\\\x00-\x1f\x7f]", path)
    ):
        raise GithubError(f"{path[:80]!r} is not a file path inside the repo")


def _reset_at(response: httpx.Response) -> datetime | None:
    """From `retry-after` (seconds from now) when GitHub sends it, a secondary limit's own wait;
    else from `x-ratelimit-reset` (epoch seconds) when the primary limit is spent. Every answer
    carries `x-ratelimit-reset`, so it says nothing about a secondary limit."""
    headers = response.headers
    reset, retry = headers.get("x-ratelimit-reset"), headers.get("retry-after")
    try:
        if retry:
            return datetime.now(UTC) + timedelta(seconds=int(retry))
        if reset and headers.get("x-ratelimit-remaining") == "0":
            return datetime.fromtimestamp(int(reset), UTC)
    except ValueError, OverflowError, OSError:
        return None
    return None


def _rate_limited(response: httpx.Response) -> bool:
    """429, or a 403 that is a primary or secondary rate limit: GitHub sends some secondary ones
    with neither header, and only its message says so."""
    if response.status_code == 429:
        return True
    return response.status_code == 403 and (
        response.headers.get("x-ratelimit-remaining") == "0"
        or "retry-after" in response.headers
        or "rate limit" in response.text.lower()
    )


def _wanted(entry: _RawEntry, suffixes: tuple[str, ...]) -> bool:
    return not suffixes or entry.path.lower().endswith(tuple(s.lower() for s in suffixes))


def _decode(raw: _RawFile) -> str:
    if raw.encoding != "base64" or raw.content is None:
        raise TooLarge(f"{raw.path} is too large to read through the API")
    try:
        return base64.b64decode(raw.content).decode("utf-8", errors="replace")
    except binascii.Error as error:
        raise GithubError(f"{raw.path} came back in an unreadable encoding") from error


class GithubClient:
    def __init__(
        self,
        token: SecretStr | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str = API,
        timeout_s: float = TIMEOUT_S,
    ) -> None:
        self._token = token if token is not None and token.get_secret_value() else None
        self._transport = transport
        self._base_url = base_url
        self._timeout_s = timeout_s

    @property
    def authenticated(self) -> bool:
        return self._token is not None

    async def commits(
        self, repo: str, since: datetime, path: str | None = None, limit: int = 20
    ) -> list[Commit]:
        """The repo's commits on its default branch since `since`, newest first, optionally only
        those touching `path` (a file or folder)."""
        check_repo(repo)
        params = {
            "since": since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "per_page": str(max(1, min(limit, MAX_PER_PAGE))),
        }
        if path:
            params["path"] = path[:MAX_PATH]
        data = await self._get(f"/repos/{repo}/commits", params, f"repo {repo}", "repo")
        return [c.summary() for c in _parse(list[_RawCommit], data)]

    async def commit(self, repo: str, sha: str) -> CommitDetail:
        """One commit with every file it changed and each file's patch."""
        check_repo(repo)
        check_sha(sha)
        try:
            data = await self._get(
                f"/repos/{repo}/commits/{sha}", None, f"commit {sha} in {repo}", "commit"
            )
        except NotFound as missing:
            # GitHub answers 422 for an unknown sha and 404 for a repo it cannot show.
            if missing.status == 404:
                raise NotFound(f"GitHub has no repo {repo}", "repo") from missing
            raise
        raw = _parse(_RawCommit, data)
        stats = raw.stats or _Stats(
            additions=sum(f.additions for f in raw.files),
            deletions=sum(f.deletions for f in raw.files),
        )
        return CommitDetail(
            **raw.summary().model_dump(),
            files=raw.files,
            additions=stats.additions,
            deletions=stats.deletions,
        )

    async def readme(self, repo: str) -> RepoFile:
        """The repo's README, whatever its file name. `NotFound` when it has none."""
        check_repo(repo)
        data = await self._get(f"/repos/{repo}/readme", None, f"README in {repo}", "file")
        return _file(_parse(_RawFile, data))

    async def list_files(
        self,
        repo: str,
        path: str = "docs",
        max_files: int = 200,
        suffixes: tuple[str, ...] = (),
    ) -> list[RepoEntry]:
        """Every file under the folder `path`, sub-folders included, at most `max_files` and
        `MAX_FOLDERS` folders; with `suffixes`, only the files whose name ends with one of them
        (any case), and only those count toward `max_files`. `NotFound` when `path` is not there;
        a sub-folder that vanished while it was listed is skipped."""
        check_repo(repo)
        check_path(path)
        found: list[RepoEntry] = []
        folders = [path]
        opened = 0
        while folders and opened < MAX_FOLDERS and len(found) < max_files:
            folder = folders.pop(0)
            opened += 1
            try:
                data = await self._get(
                    f"/repos/{repo}/contents/{quote(folder)}", None, f"{folder} in {repo}", "file"
                )
            except NotFound:
                if folder == path:
                    raise
                continue
            if not isinstance(data, list):
                raise GithubError(f"{folder} in {repo} is a file, not a folder")
            for entry in _parse(list[_RawEntry], data):
                if entry.type == "dir":
                    folders.append(entry.path)
                elif entry.type == "file" and len(found) < max_files and _wanted(entry, suffixes):
                    found.append(
                        RepoEntry(path=entry.path, type="file", size=entry.size, url=entry.html_url)
                    )
        if folders or len(found) >= max_files:
            log.warning(
                "github: listed %d files under %s in %s and stopped (at most %d files and %d "
                "folders); the rest is not listed",
                len(found), path, repo, max_files, MAX_FOLDERS,
            )  # fmt: skip
        return found

    async def read_file(self, repo: str, path: str) -> RepoFile:
        """One file's text (GitHub serves files up to 1 MB this way)."""
        check_repo(repo)
        check_path(path)
        data = await self._get(
            f"/repos/{repo}/contents/{quote(path)}", None, f"{path} in {repo}", "file"
        )
        if not isinstance(data, dict) or data.get("type") != "file":
            raise GithubError(f"{path} in {repo} is not a file")
        return _file(_parse(_RawFile, data))

    async def _get(
        self,
        url: str,
        params: dict[str, str] | None,
        what: str,
        kind: Literal["repo", "commit", "file"],
    ) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "bleep",
        }
        if self._token is not None:
            headers["Authorization"] = f"Bearer {self._token.get_secret_value()}"
        async with httpx.AsyncClient(
            base_url=self._base_url,
            transport=self._transport,
            timeout=self._timeout_s,
            headers=headers,
            follow_redirects=True,  # a renamed repo; httpx drops the token off api.github.com
        ) as http:
            try:
                response = await http.get(url, params=params)
            except httpx.HTTPError as error:
                raise GithubUnavailable(
                    f"GitHub could not be reached ({type(error).__name__})"
                ) from error
        status = response.status_code
        if status == 200:
            try:
                return response.json()
            except ValueError as error:
                raise GithubError(f"GitHub's answer for {what} is not JSON") from error
        if status in (404, 422):
            raise NotFound(f"GitHub has no {what}", kind, status)
        if _rate_limited(response):
            raise RateLimited("GitHub's rate limit is spent", _reset_at(response))
        if status == 401:
            raise GithubError("GitHub refused the token (401)")
        if status >= 500:
            raise GithubUnavailable(f"GitHub answered {status}")
        raise GithubError(f"GitHub answered {status} for {what}")


def _parse[T](shape: type[T], data: Any) -> T:
    try:
        return TypeAdapter(shape).validate_python(data)
    except ValidationError as error:
        problems = error.error_count()
        raise GithubError(f"GitHub's answer has an unexpected shape: {problems} errors") from error


def _file(raw: _RawFile) -> RepoFile:
    return RepoFile(path=raw.path, sha=raw.sha, text=_decode(raw), url=raw.html_url)
