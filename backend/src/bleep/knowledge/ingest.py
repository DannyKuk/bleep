"""What goes into knowledge search, and when.

Three sources: the Docker docs snapshot shipped with Bleep (`backend/knowledge/docker-docs/`), the
README and `docs/` of each service's linked GitHub repo, and the incidents in long-term memory.
`KnowledgeRefresher.run` syncs the snapshot once at start, past incidents every 10 minutes and
repo docs once a day. Re-ingest is skipped by content hash, so a boot with nothing new embeds
nothing. A source that fails is logged and keeps what it had; Bleep runs on without it.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

import yaml

from bleep.hosts.github import RateLimited
from bleep.knowledge.index import KnowledgeIndex, SyncResult
from bleep.schemas.github import RepoFile
from bleep.schemas.graph import IncidentRecord
from bleep.schemas.knowledge import KnowledgeDocument

log = logging.getLogger(__name__)

# The snapshot sits beside src/ in the repo and in the image (/app/knowledge/docker-docs).
SNAPSHOT_DIR = Path(__file__).resolve().parents[3] / "knowledge" / "docker-docs"
NOT_PAGES = {"README.md"}
INCIDENTS_EVERY_S = 600.0
REPOS_EVERY_S = 86_400.0
MAX_REPO_FILES = 40
MAX_REPO_FILE_CHARS = 100_000
NOTHING = SyncResult(embedded=0, unchanged=0, removed=0)


class SnapshotError(Exception):
    """A snapshot page lacks the front matter every page must carry."""


class IncidentReports(Protocol):
    async def all_records(self) -> list[IncidentRecord]: ...


class RepoDocs(Protocol):
    """The linked repos and their README and `docs/**/*.md`, read through the GitHub client."""

    async def linked_repos(self) -> list[str]:
        """`owner/name` of every repo a service links."""
        ...

    async def doc_files(self, repo: str) -> list[RepoFile]:
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
        missing = [key for key in ("title", "source_url", "license") if not meta.get(key)]
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

    async def refresh_docker(self) -> SyncResult:
        if self.snapshot_dir is None:
            return NOTHING
        return await self.index.sync("docker", load_snapshot(self.snapshot_dir))

    async def refresh_incidents(self) -> SyncResult:
        if self.reports is None:
            return NOTHING
        records = await self.reports.all_records()
        return await self.index.sync("incident", [incident_document(r) for r in records])

    async def refresh_repos(self) -> int:
        """Sync every linked repo's docs; how many documents were embedded. A repo that cannot be
        read keeps the docs it had; a repo no longer linked loses them. A rate limit stops the
        refresh there: without a token GitHub allows 60 requests an hour, and the rest would fail
        too."""
        if self.repos is None:
            return 0
        linked = await self.repos.linked_repos()
        embedded = 0
        for repo in linked:
            try:
                files = await self.repos.doc_files(repo)
            except RateLimited as error:
                log.warning("knowledge: GitHub rate limit reached at %s (%s)", repo, error)
                break
            except Exception as error:  # the next daily refresh tries again
                log.warning("knowledge: could not read the docs of %s (%s)", repo, error)
                continue
            prefix = repo_prefix(repo)
            embedded += (
                await self.index.sync("repo", repo_documents(repo, files), prefix)
            ).embedded
        kept = tuple(repo_prefix(repo) for repo in linked)
        await self.index.remove(
            i for i in await self.index.document_ids("repo:") if not i.startswith(kept)
        )
        return embedded

    async def _safely(self, name: str, refresh: Callable[[], Awaitable[object]]) -> None:
        try:
            result = await refresh()
        except Exception as error:  # a source that fails never stops Bleep
            log.warning("knowledge: refreshing %s failed (%s)", name, error)
            return
        log.info("knowledge: refreshed %s: %s", name, result)

    async def run(self) -> None:
        """Sync the snapshot, then keep incidents and repo docs fresh until cancelled."""
        await self._safely("docker docs", self.refresh_docker)
        due_incidents = due_repos = self.monotonic()
        while True:
            now = self.monotonic()
            if now >= due_incidents:
                await self._safely("past incidents", self.refresh_incidents)
                due_incidents = now + self.incidents_every_s
            if now >= due_repos:
                await self._safely("repo docs", self.refresh_repos)
                due_repos = now + self.repos_every_s
            await self.sleep(max(0.0, min(due_incidents, due_repos) - self.monotonic()))
