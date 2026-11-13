"""Reading the repos' docs on a person's press (#556): one refresh at a time, and the state."""

import asyncio

import pytest
from test_ingest import Repos, readme

from bleep.knowledge.index import KnowledgeIndex
from bleep.knowledge.ingest import KnowledgeRefresher, RepoDocFiles, RepoRefresh
from bleep.memory.embeddings import FakeEmbedder
from bleep.schemas.github import RepoFile
from bleep.store.db import Database


@pytest.fixture
async def index(tmp_path) -> KnowledgeIndex:
    db = Database(tmp_path / "bleep.db")
    await db.migrate()
    return KnowledgeIndex(db, FakeEmbedder())


class GatedRepos(Repos):
    """Repos whose reads wait until a test lets them go."""

    def __init__(self, files: dict[str, list[RepoFile]]) -> None:
        super().__init__(files)
        self.gate = asyncio.Event()

    async def doc_files(self, repo: str) -> RepoDocFiles:
        await self.gate.wait()
        return await super().doc_files(repo)


class UnreadableLinks(Repos):
    async def linked_repos(self) -> list[str]:
        raise RuntimeError("the hosts file is unreadable")


async def test_a_press_while_a_refresh_runs_joins_it_instead_of_reading_again(index):
    repos = GatedRepos({"acme/shop": [readme("acme/shop")]})
    refresher = KnowledgeRefresher(index, repos=repos, snapshot_dir=None)

    daily = asyncio.create_task(refresher.refresh_repos())
    await asyncio.sleep(0)
    press = asyncio.create_task(refresher.refresh_repos_now())
    await asyncio.sleep(0)
    repos.gate.set()

    assert await daily == RepoRefresh(embedded=1, read=1, linked=1)
    result = await press
    assert (result.read, result.linked, result.embedded) == (1, 1, 1)
    assert repos.asked == ["acme/shop"]

    await refresher.refresh_repos()
    assert repos.asked == ["acme/shop", "acme/shop"]  # once it is done, the next press reads


async def test_two_presses_at_once_read_each_repo_once(index):
    repos = GatedRepos({"acme/shop": [readme("acme/shop")], "acme/blog": [readme("acme/blog")]})
    refresher = KnowledgeRefresher(index, repos=repos, snapshot_dir=None)

    presses = [asyncio.create_task(refresher.refresh_repos_now()) for _ in range(2)]
    await asyncio.sleep(0)
    repos.gate.set()
    first, second = await asyncio.gather(*presses)

    assert sorted(repos.asked) == ["acme/blog", "acme/shop"]
    assert first == second


async def test_a_refresh_that_failed_does_not_block_the_next_one(index):
    refresher = KnowledgeRefresher(index, repos=UnreadableLinks({}), snapshot_dir=None)
    with pytest.raises(RuntimeError):
        await refresher.refresh_repos()

    refresher.repos = Repos({"acme/shop": [readme("acme/shop")]})
    assert (await refresher.refresh_repos()).read == 1


async def test_the_state_lists_each_linked_repo_with_its_chunks_and_when_they_were_read(index):
    repos = Repos({"acme/shop": [readme("acme/shop")], "acme/blog": [readme("acme/blog")]})
    refresher = KnowledgeRefresher(index, repos=repos, snapshot_dir=None)

    before = await refresher.repo_docs_state()
    assert before.last_read_at is None
    assert [(r.repo, r.chunks) for r in before.repos] == [("acme/blog", 0), ("acme/shop", 0)]

    result = await refresher.refresh_repos_now()

    assert (result.read, result.linked, result.embedded) == (2, 2, 2)
    assert result.last_read_at is not None
    assert [(r.repo, r.chunks) for r in result.repos] == [("acme/blog", 1), ("acme/shop", 1)]
    after = await refresher.repo_docs_state()
    assert (after.last_read_at, after.repos) == (result.last_read_at, result.repos)


async def test_a_press_reads_repos_the_daily_refresh_would_skip_after_a_restart(index):
    repos = Repos({"acme/shop": [readme("acme/shop", "# Shop\n\nHOST is wrong")]})
    refresher = KnowledgeRefresher(index, repos=repos, snapshot_dir=None)
    await refresher.refresh_repos_now()
    assert await refresher._repos_wait() > 0  # a restart now would wait out the day

    repos.files = {"acme/shop": [readme("acme/shop", "# Shop\n\nSet HOST to the proxy name")]}
    result = await refresher.refresh_repos_now()

    assert result.embedded == 1
    [hit] = await index.search("what to set HOST to", sources={"repo"})
    assert "proxy name" in hit.text


async def test_a_press_that_reads_no_repo_keeps_the_old_time_and_docs(index):
    repos = Repos({"acme/shop": [readme("acme/shop")]})
    refresher = KnowledgeRefresher(index, repos=repos, snapshot_dir=None)
    first = await refresher.refresh_repos_now()

    repos.failing = {"acme/shop"}
    again = await refresher.refresh_repos_now()

    assert (again.read, again.linked) == (0, 1)
    assert again.last_read_at == first.last_read_at
    assert [(r.repo, r.chunks) for r in again.repos] == [("acme/shop", 1)]


async def test_without_a_github_link_the_state_is_empty(index):
    refresher = KnowledgeRefresher(index, snapshot_dir=None)

    state = await refresher.repo_docs_state()
    assert (state.last_read_at, state.repos) == (None, [])
    result = await refresher.refresh_repos_now()
    assert (result.read, result.linked, result.embedded) == (0, 0, 0)
