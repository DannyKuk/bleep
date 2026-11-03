import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bleep.knowledge.index import KnowledgeIndex
from bleep.knowledge.ingest import (
    MAX_REPO_FILE_CHARS,
    MAX_REPO_FILES,
    SNAPSHOT_DIR,
    KnowledgeRefresher,
    SnapshotError,
    incident_document,
    load_snapshot,
    repo_documents,
)
from bleep.memory.embeddings import FakeEmbedder
from bleep.schemas.github import RepoFile
from bleep.schemas.graph import IncidentRecord
from bleep.store.db import Database

PAGE = """---
title: Volumes
source_url: https://docs.docker.com/engine/storage/volumes/
source_path: content/manuals/engine/storage/volumes.md
commit: abc
fetched: '2026-11-03'
license: Apache-2.0
---

# Volumes

A named volume outlives its container.
"""
RECORD = IncidentRecord(
    incident_id="inc_1",
    host_id="a",
    service_id="a/compose:shop",
    date=datetime(2026, 11, 2, 12, tzinfo=UTC),
    title="shop-web exits: database connection refused",
    cause="shop-db was stopped by hand",
    action="docker.start shop-db on a: done",
    outcome="Fixed and verified",
    human_decision="approved docker.start shop-db on 2026-11-02",
    feedback="the diagnosis was right",
)


def snapshot(tmp_path: Path, pages: dict[str, str]) -> Path:
    folder = tmp_path / "docker-docs"
    folder.mkdir()
    for name, text in pages.items():
        (folder / name).write_text(text, encoding="utf-8")
    return folder


class Reports:
    def __init__(self, records: list[IncidentRecord]) -> None:
        self.records = records

    async def all_records(self) -> list[IncidentRecord]:
        return list(self.records)


class Repos:
    """Linked repos and their doc files; a repo in `failing` raises like an unreachable API."""

    def __init__(self, files: dict[str, list[RepoFile]], failing: set[str] = frozenset()) -> None:
        self.files, self.failing = files, set(failing)

    async def linked_repos(self) -> list[str]:
        return list(self.files)

    async def doc_files(self, repo: str) -> list[RepoFile]:
        if repo in self.failing:
            raise RuntimeError("GitHub answered 503")
        return self.files[repo]


def readme(repo: str, text: str = "# Shop\n\nRun `make db` before the web.") -> RepoFile:
    return RepoFile(
        path="README.md",
        sha="0" * 40,
        url=f"https://github.com/{repo}/blob/main/README.md",
        text=text,
    )


@pytest.fixture
async def index(tmp_path) -> KnowledgeIndex:
    db = Database(tmp_path / "bleep.db")
    await db.migrate()
    return KnowledgeIndex(db, FakeEmbedder())


def test_a_snapshot_page_keeps_its_front_matter_as_title_and_url(tmp_path):
    folder = snapshot(tmp_path, {"volumes.md": PAGE, "README.md": "# About", "LICENSE": "x"})

    [doc] = load_snapshot(folder)

    assert doc.id == "docker:volumes"
    assert doc.source == "docker"
    assert doc.title == "Volumes"
    assert doc.url == "https://docs.docker.com/engine/storage/volumes/"
    assert doc.text.startswith("# Volumes\n\nA named volume")
    assert "license" not in doc.text


@pytest.mark.parametrize(
    "text",
    ["# no front matter\n", "---\ntitle: T\n---\nbody\n", "---\n: [\n---\nbody\n"],
)
def test_a_page_without_a_title_source_and_licence_is_refused(tmp_path, text):
    folder = snapshot(tmp_path, {"bad.md": text})

    with pytest.raises(SnapshotError, match="bad.md"):
        load_snapshot(folder)


def test_the_shipped_snapshot_is_about_25_licensed_pages():
    docs = load_snapshot(SNAPSHOT_DIR)

    assert 20 <= len(docs) <= 30
    assert all(d.url.startswith("https://docs.docker.com/") for d in docs)
    assert len({d.id for d in docs}) == len(docs)
    assert "Apache License" in (SNAPSHOT_DIR / "LICENSE").read_text()


def test_a_past_incident_reads_as_a_short_report():
    doc = incident_document(RECORD)

    assert doc.id == "incident:inc_1"
    assert doc.source == "incident"
    assert doc.url == "/incidents/inc_1"
    assert doc.title == RECORD.title
    for part in (RECORD.cause, RECORD.action, RECORD.outcome, "a/compose:shop", "2026-11-02"):
        assert part in doc.text
    assert "the human approved docker.start" in doc.text.lower()


def test_repo_files_become_documents_capped_in_number_and_size():
    files = [readme("acme/shop", "x" * (MAX_REPO_FILE_CHARS + 500))] + [
        RepoFile(path=f"docs/p{n}.md", sha="0" * 40, url=None, text=f"page {n}")
        for n in range(MAX_REPO_FILES + 5)
    ]

    docs = repo_documents("acme/shop", files)

    assert len(docs) == MAX_REPO_FILES
    assert docs[0].id == "repo:acme/shop:README.md"
    assert docs[0].title == "acme/shop: README.md"
    assert docs[0].source == "repo"
    assert len(docs[0].text) == MAX_REPO_FILE_CHARS
    assert docs[1].url == "https://github.com/acme/shop/blob/HEAD/docs/p0.md"  # none given


async def test_refreshing_the_snapshot_twice_embeds_it_once(index, tmp_path):
    folder = snapshot(tmp_path, {"volumes.md": PAGE})
    refresher = KnowledgeRefresher(index, snapshot_dir=folder)

    first = await refresher.refresh_docker()
    second = await refresher.refresh_docker()

    assert (first.embedded, second.embedded, second.unchanged) == (1, 0, 1)


async def test_incidents_follow_memory_including_forgotten_ones(index):
    reports = Reports([RECORD])
    refresher = KnowledgeRefresher(index, reports=reports, snapshot_dir=None)
    await refresher.refresh_incidents()
    reports.records = [RECORD.model_copy(update={"incident_id": "inc_2"})]

    result = await refresher.refresh_incidents()

    assert (result.embedded, result.removed) == (1, 1)
    assert await index.document_ids("incident:") == {"incident:inc_2"}


async def test_a_repo_that_fails_keeps_its_old_docs_and_an_unlinked_one_loses_them(index):
    repos = Repos({"acme/shop": [readme("acme/shop")], "acme/blog": [readme("acme/blog")]})
    refresher = KnowledgeRefresher(index, repos=repos, snapshot_dir=None)
    await refresher.refresh_repos()

    repos.files = {"acme/shop": [readme("acme/shop", "# Shop\n\nchanged")]}
    repos.failing = {"acme/shop"}
    await refresher.refresh_repos()

    assert await index.document_ids("repo:") == {"repo:acme/shop:README.md"}
    [hit] = await index.search("make db before the web", sources={"repo"})
    assert "make db" in hit.text


async def test_without_sources_the_refreshes_do_nothing(index):
    refresher = KnowledgeRefresher(index, snapshot_dir=None)

    assert (await refresher.refresh_docker()).embedded == 0
    assert (await refresher.refresh_incidents()).embedded == 0
    assert await refresher.refresh_repos() == 0


async def test_the_loop_refreshes_on_schedule_and_survives_a_failing_source(index, tmp_path):
    folder = snapshot(tmp_path, {"volumes.md": PAGE})
    repos = Repos({"acme/shop": [readme("acme/shop")]}, failing={"acme/shop"})
    reports = Reports([RECORD])
    now = [0.0]
    naps: list[float] = []

    async def sleep(seconds: float) -> None:
        naps.append(seconds)
        now[0] += seconds
        if len(naps) == 3:
            raise asyncio.CancelledError

    refresher = KnowledgeRefresher(
        index,
        snapshot_dir=folder,
        reports=reports,
        repos=repos,
        incidents_every_s=600,
        repos_every_s=86_400,
        sleep=sleep,
        monotonic=lambda: now[0],
    )
    with pytest.raises(asyncio.CancelledError):
        await refresher.run()

    assert naps == [600, 600, 600]
    assert await index.document_ids("docker:") == {"docker:volumes"}
    assert await index.document_ids("incident:") == {"incident:inc_1"}
    assert await index.document_ids("repo:") == set()
