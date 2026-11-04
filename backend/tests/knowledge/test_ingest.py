import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bleep.hosts.github import RateLimited
from bleep.knowledge.index import KnowledgeIndex
from bleep.knowledge.ingest import (
    MAX_REPO_FILE_CHARS,
    MAX_REPO_FILES,
    SNAPSHOT_DIR,
    KnowledgeRefresher,
    RepoDocFiles,
    RepoRefresh,
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
    """Linked repos and their doc files; a repo in `failing` raises like an unreachable API, one
    in `limited` like a spent rate limit, and the paths in `unread` could not be read."""

    def __init__(self, files: dict[str, list[RepoFile]], failing: set[str] = frozenset()) -> None:
        self.files, self.failing = files, set(failing)
        self.limited: set[str] = set()
        self.unread: dict[str, list[str]] = {}
        self.asked: list[str] = []

    async def linked_repos(self) -> list[str]:
        return list(self.files)

    async def doc_files(self, repo: str) -> RepoDocFiles:
        self.asked.append(repo)
        if repo in self.limited:
            raise RateLimited("GitHub's rate limit is spent")
        if repo in self.failing:
            raise RuntimeError("GitHub answered 503")
        return RepoDocFiles(files=self.files[repo], unread=self.unread.get(repo, []))


class FlakyEmbedder(FakeEmbedder):
    """An embedder whose first `failures` calls fail, as a model server still starting does."""

    def __init__(self, failures: int = 1) -> None:
        super().__init__()
        self.failures = failures

    def embed(self, texts: list[str]):
        if self.failures:
            self.failures -= 1
            raise ConnectionError("the embedder is not up yet")
        return super().embed(texts)


class BrokenEmbedder(FakeEmbedder):
    """An embedder that never works."""

    def embed(self, texts: list[str]):
        raise ConnectionError("the embedder is down")


DAY = 86_400.0


def run_for(now: list[float], seconds: float):
    """A sleep that moves the clock `now` and stops the loop once `seconds` have passed."""

    async def sleep(nap: float) -> None:
        now[0] += nap
        if now[0] >= seconds:
            raise asyncio.CancelledError

    return sleep


async def run_days(index, repos, now, days: float, snapshot_dir=None) -> None:
    refresher = KnowledgeRefresher(
        index,
        repos=repos,
        snapshot_dir=snapshot_dir,
        incidents_every_s=600,
        repos_every_s=DAY,
        sleep=run_for(now, days * DAY),
        monotonic=lambda: now[0],
    )
    with pytest.raises(asyncio.CancelledError):
        await refresher.run()


def stop_after(naps: list[float], now: list[float], count: int):
    """A sleep that moves the clock `now` and stops the loop after `count` naps."""

    async def sleep(seconds: float) -> None:
        naps.append(seconds)
        now[0] += seconds
        if len(naps) == count:
            raise asyncio.CancelledError

    return sleep


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
    [
        "# no front matter\n",
        "---\ntitle: T\n---\nbody\n",
        "---\n: [\n---\nbody\n",
        "---\ntitle: T\nsource_url: u\nlicense: Apache-2.0\n---\nno fetch date\n",
    ],
)
def test_a_page_without_a_title_source_and_licence_is_refused(tmp_path, text):
    folder = snapshot(tmp_path, {"bad.md": text})

    with pytest.raises(SnapshotError, match="bad.md"):
        load_snapshot(folder)


def test_the_shipped_snapshot_is_27_licensed_pages():
    docs = load_snapshot(SNAPSHOT_DIR)

    assert len(docs) == 27  # docs/design/knowledge.md and the snapshot's README say so
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
    assert await refresher.refresh_repos() == RepoRefresh(embedded=0, read=0, linked=0)


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


async def test_a_restart_within_the_day_does_not_read_the_repos_again(tmp_path):
    db = Database(tmp_path / "bleep.db")
    await db.migrate()
    wall = [datetime(2026, 11, 3, 12, tzinfo=UTC)]
    index = KnowledgeIndex(db, FakeEmbedder(), clock=lambda: wall[0])
    repos = Repos({"acme/shop": [readme("acme/shop")]})
    asked: list[str] = []
    real = repos.doc_files

    async def doc_files(repo: str):
        asked.append(repo)
        return await real(repo)

    repos.doc_files = doc_files  # type: ignore[method-assign]
    await KnowledgeRefresher(index, repos=repos, snapshot_dir=None).refresh_repos()
    assert await index.last_refresh("repos") == (wall[0], "acme/shop")
    wall[0] = wall[0].replace(hour=18)  # restarted six hours later
    naps: list[float] = []

    async def sleep(seconds: float) -> None:
        naps.append(seconds)
        raise asyncio.CancelledError

    restarted = KnowledgeRefresher(
        index, repos=repos, snapshot_dir=None, incidents_every_s=600, sleep=sleep,
        monotonic=lambda: 0.0,
    )  # fmt: skip
    with pytest.raises(asyncio.CancelledError):
        await restarted.run()

    assert asked == ["acme/shop"]  # only the first refresh read GitHub
    assert naps == [600]

    wall[0] = wall[0].replace(day=5)  # two days later: due at once
    with pytest.raises(asyncio.CancelledError):
        await restarted.run()
    assert asked == ["acme/shop", "acme/shop"]


async def test_a_newly_linked_repo_or_a_failed_refresh_is_read_again_at_the_next_start(tmp_path):
    db = Database(tmp_path / "bleep.db")
    await db.migrate()
    wall = [datetime(2026, 11, 3, 12, tzinfo=UTC)]
    index = KnowledgeIndex(db, FakeEmbedder(), clock=lambda: wall[0])
    repos = Repos({"acme/shop": [readme("acme/shop")]})
    await KnowledgeRefresher(index, repos=repos, snapshot_dir=None).refresh_repos()

    async def stop(_seconds: float) -> None:
        raise asyncio.CancelledError

    async def restart() -> None:
        refresher = KnowledgeRefresher(
            index, repos=repos, snapshot_dir=None, sleep=stop, monotonic=lambda: 0.0
        )
        with pytest.raises(asyncio.CancelledError):
            await refresher.run()

    wall[0] = wall[0].replace(hour=13)
    repos.files["acme/api"] = [readme("acme/api")]  # linked an hour later
    await restart()
    assert await index.document_ids("repo:acme/api:") == {"repo:acme/api:README.md"}
    assert await index.last_refresh("repos") == (wall[0], "acme/api\nacme/shop")

    wall[0] = wall[0].replace(hour=14)
    repos.failing = {"acme/api", "acme/shop"}  # GitHub unreachable: nothing read
    repos.files["acme/blog"] = [readme("acme/blog")]
    repos.failing.add("acme/blog")
    await restart()
    assert (await index.last_refresh("repos"))[0] == wall[0].replace(hour=13)  # not marked


async def test_a_snapshot_sync_that_fails_at_start_is_retried_until_it_succeeds(tmp_path):
    db = Database(tmp_path / "bleep.db")
    await db.migrate()
    embedder = FlakyEmbedder(failures=1)
    index = KnowledgeIndex(db, embedder)
    now = [0.0]
    naps: list[float] = []
    held: list[set[str]] = []
    stop = stop_after(naps, now, 3)

    async def sleep(seconds: float) -> None:
        held.append(await index.document_ids("docker:"))
        await stop(seconds)

    refresher = KnowledgeRefresher(
        index,
        snapshot_dir=snapshot(tmp_path, {"volumes.md": PAGE}),
        incidents_every_s=600,
        sleep=sleep,
        monotonic=lambda: now[0],
    )
    with pytest.raises(asyncio.CancelledError):
        await refresher.run()

    assert held == [set(), {"docker:volumes"}, {"docker:volumes"}]
    assert naps == [600, 600, 600]
    assert len(embedder.calls) == 1  # synced once it worked, and not again after


async def test_a_refresh_cut_short_marks_only_the_repos_it_read(index):
    repos = Repos({"acme/api": [readme("acme/api")], "acme/shop": [readme("acme/shop")]})
    repos.limited = {"acme/shop"}
    refresher = KnowledgeRefresher(index, repos=repos, snapshot_dir=None)

    assert await refresher.refresh_repos() == RepoRefresh(embedded=1, read=1, linked=2)
    assert (await index.last_refresh("repos"))[1] == "acme/api"

    async def stop(_seconds: float) -> None:
        raise asyncio.CancelledError

    repos.limited, repos.asked = set(), []
    restarted = KnowledgeRefresher(
        index, repos=repos, snapshot_dir=None, sleep=stop, monotonic=lambda: 0.0
    )
    with pytest.raises(asyncio.CancelledError):
        await restarted.run()
    assert repos.asked == ["acme/api", "acme/shop"]  # due at once: acme/shop was never read
    assert await index.document_ids("repo:acme/shop:") == {"repo:acme/shop:README.md"}


async def test_a_refresh_that_read_no_repo_is_retried_on_the_incidents_cadence(index):
    repos = Repos({"acme/shop": [readme("acme/shop")]}, failing={"acme/shop"})
    now = [0.0]
    naps: list[float] = []
    stop = stop_after(naps, now, 4)

    async def sleep(seconds: float) -> None:
        repos.failing = set()  # GitHub answers again after the first try
        await stop(seconds)

    refresher = KnowledgeRefresher(
        index,
        repos=repos,
        snapshot_dir=None,
        incidents_every_s=600,
        repos_every_s=86_400,
        sleep=sleep,
        monotonic=lambda: now[0],
    )
    with pytest.raises(asyncio.CancelledError):
        await refresher.run()

    assert repos.asked == ["acme/shop", "acme/shop"]  # at start, then 600 s later; then a day
    assert await index.document_ids("repo:") == {"repo:acme/shop:README.md"}


async def broken_index(tmp_path) -> KnowledgeIndex:
    db = Database(tmp_path / "bleep.db")
    await db.migrate()
    return KnowledgeIndex(db, BrokenEmbedder())


async def test_a_broken_embedder_with_no_snapshot_synced_reads_the_repos_once(tmp_path):
    repos = Repos({"acme/api": [readme("acme/api")], "acme/shop": [readme("acme/shop")]})
    now = [0.0]

    await run_days(
        await broken_index(tmp_path), repos, now, 3, snapshot(tmp_path, {"volumes.md": PAGE})
    )

    assert repos.asked == ["acme/api"] * 3  # its sync failed; a day apart while the snapshot fails


async def test_a_broken_embedder_after_the_snapshot_reads_the_repos_once_a_day(tmp_path):
    repos = Repos({"acme/shop": [readme("acme/shop")]})
    now = [0.0]

    await run_days(await broken_index(tmp_path), repos, now, 3)  # no snapshot: nothing to prove

    assert repos.asked == ["acme/shop"] * 3


async def test_repo_docs_are_read_again_once_the_snapshot_shows_the_embedder_works(tmp_path):
    db = Database(tmp_path / "bleep.db")
    await db.migrate()
    index = KnowledgeIndex(db, FlakyEmbedder(failures=2))  # the snapshot, then the README
    repos = Repos({"acme/shop": [readme("acme/shop")]})
    now = [0.0]

    await run_days(index, repos, now, 0.5, snapshot(tmp_path, {"volumes.md": PAGE}))

    assert repos.asked == ["acme/shop", "acme/shop"]  # at start, then with the snapshot at 600 s
    assert await index.document_ids("repo:") == {"repo:acme/shop:README.md"}


async def test_github_failing_for_days_is_retried_with_a_capped_backoff_and_logged_once(
    index, caplog
):
    repos = Repos({"acme/shop": [readme("acme/shop")]}, failing={"acme/shop"})
    now = [0.0]
    tried: list[float] = []
    real = repos.doc_files

    async def doc_files(repo: str):
        tried.append(now[0])
        return await real(repo)

    repos.doc_files = doc_files  # type: ignore[method-assign]
    with caplog.at_level("WARNING", logger="bleep.knowledge.ingest"):
        await run_days(index, repos, now, 3)

    gaps = [later - earlier for earlier, later in zip(tried, tried[1:], strict=False)]
    assert gaps == [600, 1200, 2400, 4800, 9600, 19200, 38400, 76800, DAY]
    warned = [r for r in caplog.records if "could not read the docs of acme/shop" in r.message]
    assert len(warned) == 1


async def test_the_repos_are_tried_a_day_apart_while_the_snapshot_keeps_failing(tmp_path):
    repos = Repos({"acme/shop": [readme("acme/shop")]})
    now = [0.0]
    asked_at: list[float] = []
    real = repos.doc_files

    async def doc_files(repo: str):
        asked_at.append(now[0])
        return await real(repo)

    repos.doc_files = doc_files  # type: ignore[method-assign]

    await run_days(
        await broken_index(tmp_path), repos, now, 3.5, snapshot(tmp_path, {"volumes.md": PAGE})
    )

    assert asked_at == [0, DAY, 2 * DAY, 3 * DAY]


def test_the_backoff_is_capped_at_a_day_for_any_failure_count():
    refresher = KnowledgeRefresher(
        None,  # type: ignore[arg-type]
        incidents_every_s=600,
        repos_every_s=DAY,
    )

    assert refresher._backoff(1) == 600
    assert refresher._backoff(100_000) == DAY


async def test_a_repo_no_longer_linked_is_forgotten_by_the_failure_log(index):
    repos = Repos({"acme/shop": [readme("acme/shop")]}, failing={"acme/shop"})
    refresher = KnowledgeRefresher(index, repos=repos, snapshot_dir=None)
    await refresher.refresh_repos()
    assert set(refresher._failing) == {"acme/shop"}

    repos.files = {"acme/api": [readme("acme/api")]}
    await refresher.refresh_repos()

    assert refresher._failing == {}
