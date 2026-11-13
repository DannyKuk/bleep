"""What goes into knowledge search, and when.

Three sources: the Docker docs snapshot shipped with Bleep (`backend/knowledge/docker-docs/`), the
README and `docs/` of each service's linked GitHub repo, and the incidents in long-term memory.
`KnowledgeRefresher.run` syncs the snapshot at start, past incidents every 10 minutes and repo
docs once a day; a snapshot sync that fails is tried again every 10 minutes, and a repo refresh
that read no repo after 10 minutes, doubling up to a day. Re-ingest is skipped by content hash,
so a boot with nothing new embeds nothing. A source that fails is logged and keeps what it had;
Bleep runs on without it.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import yaml

from bleep.hosts.github import RateLimited
from bleep.knowledge.index import KnowledgeIndex, SyncResult
from bleep.schemas.github import RepoFile
from bleep.schemas.graph import IncidentRecord
from bleep.schemas.knowledge import (
    KnowledgeDocument,
    RepoDocsRepo,
    RepoDocsResult,
    RepoDocsState,
)

log = logging.getLogger(__name__)

# The snapshot sits beside src/ in the repo and in the image (/app/knowledge/docker-docs).
SNAPSHOT_DIR = Path(__file__).resolve().parents[3] / "knowledge" / "docker-docs"
NOT_PAGES = {"README.md"}
INCIDENTS_EVERY_S = 600.0
REPOS_EVERY_S = 86_400.0
MAX_REPO_FILES = 40
MAX_REPO_FILE_CHARS = 100_000
NOTHING = SyncResult(embedded=0, unchanged=0, removed=0)
REPOS_REFRESH = "repos"
MAX_BACKOFF_EXPONENT = 32  # far past a day; keeps the power from growing without bound


class SnapshotError(Exception):
    """A snapshot page lacks the front matter every page must carry."""


class IncidentReports(Protocol):
    async def all_records(self) -> list[IncidentRecord]: ...


@dataclass(frozen=True)
class RepoDocFiles:
    """What one read of a repo's docs gave: the files it read, and the paths of the files it
    found but could not read this time, which keep the docs they had."""

    files: list[RepoFile]
    unread: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RepoRefresh:
    embedded: int
    read: int
    linked: int


class RepoDocs(Protocol):
    """The linked repos and their README and `docs/**/*.md`, read through the GitHub client."""

    async def linked_repos(self) -> list[str]:
        """`owner/name` of every repo a service links."""
        ...

    async def doc_files(self, repo: str) -> RepoDocFiles:
        """The repo's README first, then its markdown under `docs/`."""
        ...


def _front_matter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise SnapshotError(f"{path.name}: no front matter")
    head, body = text[4:].split("\n---\n", 1)
    try:
        meta = yaml.safe_load(head)
    except yaml.YAMLError as error:
        raise SnapshotError(f"{path.name}: unreadable front matter ({error})") from None
    if not isinstance(meta, dict):
        raise SnapshotError(f"{path.name}: unreadable front matter")
    return meta, body.lstrip("\n")


def load_snapshot(directory: Path = SNAPSHOT_DIR) -> list[KnowledgeDocument]:
    """The snapshot's pages, each with the title and source URL from its front matter."""
    docs = []
    for path in sorted(directory.glob("*.md")):
        if path.name in NOT_PAGES:
            continue
        meta, body = _front_matter(path)
        missing = [k for k in ("title", "source_url", "fetched", "license") if not meta.get(k)]
        if missing:
            raise SnapshotError(f"{path.name}: front matter lacks {', '.join(missing)}")
        docs.append(
            KnowledgeDocument(
                id=f"docker:{path.stem}",
                source="docker",
                title=str(meta["title"]),
                url=str(meta["source_url"]),
                text=body,
            )
        )
    return docs


def incident_document(record: IncidentRecord) -> KnowledgeDocument:
    """A remembered incident as a short report page."""
    where = record.service_id or f"host {record.host_id}"
    lines = [
        f"# {record.title}",
        "",
        f"{record.date:%Y-%m-%d} on {where}.",
        "",
        f"Cause: {record.cause}",
        f"Action: {record.action}",
        f"Outcome: {record.outcome}",
    ]
    if record.human_decision:
        lines.append(f"The human {record.human_decision}.")
    if record.feedback:
        lines.append(f"Feedback: {record.feedback}.")
    return KnowledgeDocument(
        id=f"incident:{record.incident_id}",
        source="incident",
        title=record.title,
        url=f"/incidents/{record.incident_id}",
        text="\n".join(lines),
    )


def repo_prefix(repo: str) -> str:
    return f"repo:{repo}:"


def repo_documents(repo: str, files: list[RepoFile]) -> list[KnowledgeDocument]:
    """The first `MAX_REPO_FILES` files of a repo, each cut to `MAX_REPO_FILE_CHARS`."""
    return [
        KnowledgeDocument(
            id=f"{repo_prefix(repo)}{f.path}",
            source="repo",
            title=f"{repo}: {f.path}",
            url=f.url or f"https://github.com/{repo}/blob/HEAD/{f.path}",
            text=f.text[:MAX_REPO_FILE_CHARS],
        )
        for f in files[:MAX_REPO_FILES]
    ]


def _scope(repos: list[str]) -> str:
    return "\n".join(sorted(repos))


class KnowledgeRefresher:
    def __init__(
        self,
        index: KnowledgeIndex,
        *,
        snapshot_dir: Path | None = SNAPSHOT_DIR,
        reports: IncidentReports | None = None,
        repos: RepoDocs | None = None,
        incidents_every_s: float = INCIDENTS_EVERY_S,
        repos_every_s: float = REPOS_EVERY_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.index = index
        self.snapshot_dir = snapshot_dir
        self.reports = reports
        self.repos = repos
        self.incidents_every_s = incidents_every_s
        self.repos_every_s = repos_every_s
        self.sleep = sleep
        self.monotonic = monotonic
        self._failing: dict[str, str] = {}
        self._refreshing: asyncio.Task[RepoRefresh] | None = None
        self._closed = False
        self._outcome: RepoDocsResult | None = None

    async def refresh_docker(self) -> SyncResult:
        if self.snapshot_dir is None:
            return NOTHING
        return await self.index.sync("docker", load_snapshot(self.snapshot_dir))

    async def refresh_incidents(self) -> SyncResult:
        if self.reports is None:
            return NOTHING
        records = await self.reports.all_records()
        return await self.index.sync("incident", [incident_document(r) for r in records])

    async def refresh_repos(self) -> RepoRefresh:
        """Sync every linked repo's docs now. A refresh already running (the daily one, or a
        person's press) is joined, not repeated: both get its result. A caller that is cancelled
        leaves the refresh running for the others; `close` stops it, and after it a refresh is
        cancelled at once, so none outlives the app."""
        return await asyncio.shield(self._running())

    def _running(self) -> asyncio.Task[RepoRefresh]:
        """The refresh in flight, started when none is."""
        if self._closed:
            raise asyncio.CancelledError  # the app is shutting down: nothing may start now
        if self._refreshing is None or self._refreshing.done():
            task = asyncio.ensure_future(self._read_and_note())
            task.add_done_callback(self._refresh_done)
            self._refreshing = task
        return self._refreshing

    def _refresh_done(self, task: asyncio.Task[RepoRefresh]) -> None:
        if self._refreshing is task:
            self._refreshing = None

    async def _read_and_note(self) -> RepoRefresh:
        """`_read_repos`, noting how it ended: every read does, the daily one and a person's, so
        the result shown is always the last read's. A read that is cancelled notes nothing."""
        try:
            refresh = await self._read_repos()
        except Exception:
            self._outcome = RepoDocsResult(failed=True, read=0, linked=0, embedded=0)
            raise
        self._outcome = RepoDocsResult(
            failed=False, read=refresh.read, linked=refresh.linked, embedded=refresh.embedded
        )
        return refresh

    async def _read_repos(self) -> RepoRefresh:
        """Sync every linked repo's docs. A repo that cannot be read keeps the docs it had, and so
        does a file that could not be read; a repo no longer linked loses them. A rate limit stops
        the refresh there: without a token GitHub allows 60 requests an hour, and the rest would
        fail too. The refresh is marked done with the repos it read, so a restart reads the others
        again; it is not marked when it read none of them."""
        if self.repos is None:
            return RepoRefresh(embedded=0, read=0, linked=0)
        linked = await self.repos.linked_repos()
        self._failing = {r: why for r, why in self._failing.items() if r in linked}
        embedded, read = 0, []
        for repo in linked:
            try:
                docs = await self.repos.doc_files(repo)
            except RateLimited as error:
                self._failed(repo, f"GitHub rate limit reached at {repo} ({error})")
                break
            except Exception as error:  # the next refresh tries again
                self._failed(repo, f"could not read the docs of {repo} ({error})")
                continue
            if self._failing.pop(repo, None) is not None:
                log.info("knowledge: read the docs of %s again", repo)
            read.append(repo)
            prefix = repo_prefix(repo)
            kept = {f"{prefix}{path}" for path in docs.unread}
            embedded += (
                await self.index.sync("repo", repo_documents(repo, docs.files), prefix, kept)
            ).embedded
        still_linked = tuple(repo_prefix(repo) for repo in linked)
        await self.index.remove(
            i for i in await self.index.document_ids("repo:") if not i.startswith(still_linked)
        )
        if read or not linked:
            await self.index.mark_refresh(REPOS_REFRESH, _scope(read))
        return RepoRefresh(embedded=embedded, read=len(read), linked=len(linked))

    async def repo_docs_state(self) -> RepoDocsState:
        """When the repos were last read, the chunks each linked repo has in search, whether a
        refresh is running, and how the last one ended, whoever started it."""
        # Taken first: a state that says it is not reading holds what the read left, never what
        # was there before it.
        reading, result = self._is_reading(), self._outcome
        last = await self.index.last_refresh(REPOS_REFRESH)
        linked = await self.repos.linked_repos() if self.repos is not None else []
        counts = await self.index.repo_chunk_counts()
        return RepoDocsState(
            last_read_at=last[0] if last else None,
            repos=[RepoDocsRepo(repo=repo, chunks=counts.get(repo, 0)) for repo in sorted(linked)],
            reading=reading,
            result=result,
        )

    def _is_reading(self) -> bool:
        return self._refreshing is not None and not self._refreshing.done()

    def start_repo_refresh(self) -> None:
        """Read the repos' docs in the background, at once and though they were read a moment
        ago, or join the read already running. It returns before the read does: a read can take
        over a minute, longer than a request may. `repo_docs_state` shows how it ended."""
        if self._closed:
            return
        running = self._is_reading()
        task = self._running()
        if not running:  # one that is joined is logged by whoever started it
            task.add_done_callback(self._log_failure)

    def _log_failure(self, task: asyncio.Task[RepoRefresh]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            log.warning("knowledge: reading the repo docs on request failed (%s)", error)

    async def close(self) -> None:
        """Stop a refresh in flight, and start no other."""
        self._closed = True
        if self._refreshing is not None and not self._refreshing.done():
            self._refreshing.cancel()
            await asyncio.gather(self._refreshing, return_exceptions=True)

    def _failed(self, repo: str, why: str) -> None:
        """Log why `repo` could not be read, once until the reason changes: a bad token or an
        outage would otherwise repeat the same warning at every retry."""
        if self._failing.get(repo) == why:
            log.debug("knowledge: %s, again", why)
            return
        self._failing[repo] = why
        log.warning("knowledge: %s", why)

    def _backoff(self, failures: int) -> float:
        """The wait after `failures` repo refreshes in a row that read no repo: the incidents'
        interval, doubled each time, at most a day."""
        return min(
            self.incidents_every_s * 2 ** min(failures - 1, MAX_BACKOFF_EXPONENT),
            self.repos_every_s,
        )

    async def _safely[T](self, name: str, refresh: Callable[[], Awaitable[T]]) -> T | None:
        """`refresh()`'s result, or None when it failed."""
        try:
            result = await refresh()
        except Exception as error:  # a source that fails never stops Bleep
            log.warning("knowledge: refreshing %s failed (%s)", name, error)
            return None
        log.info("knowledge: refreshed %s: %s", name, result)
        return result

    async def _repos_wait(self) -> float:
        """Seconds until repo docs are due: what is left of the day since the last refresh when
        it covered the repos linked now, so a restart costs no GitHub requests. A first start, a
        repo linked or unlinked since, a repo doc embedded by another embedder than the one in force
        (search leaves it out) and a clock that moved back are all due at once."""
        if self.repos is None:
            return 0.0
        try:
            last = await self.index.last_refresh(REPOS_REFRESH)
            linked = await self.repos.linked_repos()
            if await self.index.has_other_model("repo:"):
                return 0.0
        except Exception as error:  # an unreadable record only means refreshing early
            log.warning("knowledge: could not read the last repo refresh (%s)", error)
            return 0.0
        if last is None or last[1] != _scope(linked):
            return 0.0
        since = (self.index.clock() - last[0]).total_seconds()
        return self.repos_every_s - since if 0 <= since < self.repos_every_s else 0.0

    async def run(self) -> None:
        """Sync the snapshot, then keep incidents and repo docs fresh until cancelled.

        A snapshot sync that failed (the embedder was down, say) is tried again with every
        incidents refresh until it succeeds. A repo refresh that read no repo (GitHub unreachable,
        a refused token) is tried again after 10 minutes, then 20, 40 and so on up to a day, so a
        short outage at start does not leave search without repo docs for a day and a lasting one
        costs a few requests a day. A repo refresh that failed after reading GitHub (the embedder
        again) is tried a day later, sooner when the snapshot sync succeeds in between: each try
        reads every repo again, about 40 requests of GitHub's 60 an hour without a token."""
        start = self.monotonic()
        due_docker: float | None = start
        due_incidents = start
        due_repos = start + await self._repos_wait()
        repos_failed = False
        empty_refreshes = 0
        while True:
            now = self.monotonic()
            if due_docker is not None and now >= due_docker:
                synced = await self._safely("docker docs", self.refresh_docker)
                due_docker = None if synced is not None else now + self.incidents_every_s
                if synced is not None and repos_failed:
                    due_repos = min(due_repos, now)
                    repos_failed = False
            if now >= due_incidents:
                await self._safely("past incidents", self.refresh_incidents)
                due_incidents = now + self.incidents_every_s
            if now >= due_repos:
                repos = await self._safely("repo docs", self.refresh_repos)
                repos_failed = repos is None and due_docker is not None
                if repos is None:
                    due_repos = now + self.repos_every_s
                elif repos.linked and not repos.read:
                    empty_refreshes += 1
                    due_repos = now + self._backoff(empty_refreshes)
                else:
                    empty_refreshes = 0
                    due_repos = now + self.repos_every_s
            due = [d for d in (due_docker, due_incidents) if d is not None]
            await self.sleep(max(0.0, min(*due, due_repos) - self.monotonic()))
