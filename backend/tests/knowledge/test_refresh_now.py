"""Reading the repos' docs on a person's press (#556): one refresh at a time, and the state."""

import asyncio

import pytest
from test_ingest import Repos, readme, stop_after

from bleep.knowledge.index import KnowledgeIndex
from bleep.knowledge.ingest import KnowledgeRefresher, RepoDocFiles, RepoRefresh
from bleep.memory.embeddings import FakeEmbedder
from bleep.schemas.github import RepoFile
from bleep.schemas.knowledge import RepoDocsState
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


async def pressed(refresher: KnowledgeRefresher) -> RepoDocsState:
    """What a person's press ends with, once the background read is over."""
    refresher.start_repo_refresh()
    while (state := await refresher.repo_docs_state()).reading:
        await asyncio.sleep(0)
    return state


async def test_a_press_returns_before_the_read_ends_and_the_state_shows_it_reading(index):
    repos = GatedRepos({"acme/shop": [readme("acme/shop")]})
    refresher = KnowledgeRefresher(index, repos=repos, snapshot_dir=None)

    refresher.start_repo_refresh()
    reading = await refresher.repo_docs_state()
    assert (reading.reading, reading.result) == (True, None)

    repos.gate.set()
    while (state := await refresher.repo_docs_state()).reading:
        await asyncio.sleep(0)
    assert state.result is not None
    assert (state.result.failed, state.result.read, state.result.linked) == (False, 1, 1)
    assert state.result.embedded == 1


async def test_a_press_while_a_refresh_runs_joins_it_instead_of_reading_again(index):
    repos = GatedRepos({"acme/shop": [readme("acme/shop")]})
    refresher = KnowledgeRefresher(index, repos=repos, snapshot_dir=None)

    daily = asyncio.create_task(refresher.refresh_repos())
    await asyncio.sleep(0)
    refresher.start_repo_refresh()
    refresher.start_repo_refresh()
    assert (await refresher.repo_docs_state()).reading
    repos.gate.set()

    assert await daily == RepoRefresh(embedded=1, read=1, linked=1)
    state = await pressed(refresher)
    assert state.result is not None and state.result.read == 1
    assert repos.asked[:1] == ["acme/shop"]
    assert repos.asked.count("acme/shop") == 2  # the press after it is done reads again


async def test_two_presses_at_once_read_each_repo_once(index):
    repos = GatedRepos({"acme/shop": [readme("acme/shop")], "acme/blog": [readme("acme/blog")]})
    refresher = KnowledgeRefresher(index, repos=repos, snapshot_dir=None)

    refresher.start_repo_refresh()
    refresher.start_repo_refresh()
    repos.gate.set()
    while (await refresher.repo_docs_state()).reading:
        await asyncio.sleep(0)

    assert sorted(repos.asked) == ["acme/blog", "acme/shop"]


async def test_a_caller_cancelled_while_it_waits_does_not_stop_the_refresh_or_the_daily_loop(index):
    repos = GatedRepos({"acme/shop": [readme("acme/shop")]})
    naps: list[float] = []
    now = [0.0]
    refresher = KnowledgeRefresher(
        index,
        repos=repos,
        snapshot_dir=None,
        sleep=stop_after(naps, now, 1),
        monotonic=lambda: now[0],
    )
    loop = asyncio.create_task(refresher.run())
    await asyncio.sleep(0.01)  # the loop has started the day's refresh and waits on it
    press = asyncio.create_task(refresher.refresh_repos())
    await asyncio.sleep(0)
    press.cancel()
    await asyncio.sleep(0)
    assert not loop.done()

    repos.gate.set()
    with pytest.raises(asyncio.CancelledError):  # stop_after ends the loop at its first nap
        await loop

    assert naps  # it got past the refresh to its nap: the cancelled press did not end it
    assert await index.document_ids("repo:") == {"repo:acme/shop:README.md"}
    assert repos.asked == ["acme/shop"]


async def test_close_stops_a_refresh_in_flight(index):
    repos = GatedRepos({"acme/shop": [readme("acme/shop")]})
    refresher = KnowledgeRefresher(index, repos=repos, snapshot_dir=None)
    refresher.start_repo_refresh()
    await asyncio.sleep(0)

    await refresher.close()

    assert not (await refresher.repo_docs_state()).reading


@pytest.fixture
def embedder_down(index) -> dict[str, bool]:
    """A switch for the embedder: a read that has something new to embed fails while it is down."""
    real = index.embedder.embed
    switch = {"down": False}

    def embed(texts):
        if switch["down"]:
            raise ConnectionError("the embedder is down")
        return real(texts)

    index.embedder.embed = embed  # type: ignore[method-assign]
    return switch


async def test_a_refresh_that_failed_is_noted_and_logged_and_does_not_block_the_next(
    index, embedder_down, caplog
):
    repos = Repos({"acme/shop": [readme("acme/shop")]})
    refresher = KnowledgeRefresher(index, repos=repos, snapshot_dir=None)
    embedder_down["down"] = True
    with caplog.at_level("WARNING", logger="bleep.knowledge.ingest"):
        failed = await pressed(refresher)

    assert failed.result is not None and failed.result.failed
    assert caplog.text.count("the embedder is down") == 1
    embedder_down["down"] = False
    state = await pressed(refresher)
    assert state.result is not None and not state.result.failed and state.result.read == 1


async def test_the_result_is_the_last_reads_whoever_started_it(index, embedder_down):
    repos = Repos({"acme/shop": [readme("acme/shop")]})
    refresher = KnowledgeRefresher(index, repos=repos, snapshot_dir=None)
    embedder_down["down"] = True
    failed = await pressed(refresher)  # a press fails ...
    assert failed.result is not None and failed.result.failed

    embedder_down["down"] = False
    await refresher.refresh_repos()  # ... and the daily read after it works
    daily = await refresher.repo_docs_state()
    assert daily.result is not None and not daily.result.failed and daily.result.read == 1

    repos.files = {"acme/shop": [readme("acme/shop", "# Shop\n\nchanged")]}
    embedder_down["down"] = True
    with pytest.raises(ConnectionError):
        await refresher.refresh_repos()  # the daily read fails after a read that worked
    state = await refresher.repo_docs_state()
    assert state.result is not None and state.result.failed


async def test_a_read_cancelled_notes_nothing_and_none_starts_after_close(index):
    repos = GatedRepos({"acme/shop": [readme("acme/shop")]})
    refresher = KnowledgeRefresher(index, repos=repos, snapshot_dir=None)
    refresher.start_repo_refresh()
    await asyncio.sleep(0)

    await refresher.close()

    assert (await refresher.repo_docs_state()).result is None
    refresher.start_repo_refresh()  # a press landing at shutdown
    with pytest.raises(asyncio.CancelledError):  # the daily loop asking for its read
        await refresher.refresh_repos()
    assert not (await refresher.repo_docs_state()).reading
    assert repos.asked == []


async def test_a_press_just_after_a_read_ended_starts_a_new_read_that_is_noted(index):
    repos = Repos({"acme/shop": [readme("acme/shop")]})
    refresher = KnowledgeRefresher(index, repos=repos, snapshot_dir=None)
    await refresher.refresh_repos()  # the task is done, though its callback may not have run yet

    repos.files = {"acme/shop": [readme("acme/shop", "# Shop\n\nchanged")]}
    state = await pressed(refresher)

    assert state.result is not None and state.result.embedded == 1


async def test_the_state_lists_each_linked_repo_with_its_chunks_and_when_they_were_read(index):
    repos = Repos({"acme/shop": [readme("acme/shop")], "acme/blog": [readme("acme/blog")]})
    refresher = KnowledgeRefresher(index, repos=repos, snapshot_dir=None)

    before = await refresher.repo_docs_state()
    assert before.last_read_at is None and not before.reading and before.result is None
    assert [(r.repo, r.chunks) for r in before.repos] == [("acme/blog", 0), ("acme/shop", 0)]

    state = await pressed(refresher)

    assert state.result is not None
    assert (state.result.read, state.result.linked, state.result.embedded) == (2, 2, 2)
    assert state.last_read_at is not None
    assert [(r.repo, r.chunks) for r in state.repos] == [("acme/blog", 1), ("acme/shop", 1)]


async def test_a_press_reads_repos_the_daily_refresh_would_skip_after_a_restart(index):
    repos = Repos({"acme/shop": [readme("acme/shop", "# Shop\n\nHOST is wrong")]})
    refresher = KnowledgeRefresher(index, repos=repos, snapshot_dir=None)
    await pressed(refresher)
    assert await refresher._repos_wait() > 0  # a restart now would wait out the day

    repos.files = {"acme/shop": [readme("acme/shop", "# Shop\n\nSet HOST to the proxy name")]}
    state = await pressed(refresher)

    assert state.result is not None and state.result.embedded == 1
    [hit] = await index.search("what to set HOST to", sources={"repo"})
    assert "proxy name" in hit.text


async def test_a_press_that_reads_no_repo_keeps_the_old_time_and_docs(index):
    repos = Repos({"acme/shop": [readme("acme/shop")]})
    refresher = KnowledgeRefresher(index, repos=repos, snapshot_dir=None)
    first = await pressed(refresher)

    repos.failing = {"acme/shop"}
    again = await pressed(refresher)

    assert again.result is not None and (again.result.read, again.result.linked) == (0, 1)
    assert again.last_read_at == first.last_read_at
    assert [(r.repo, r.chunks) for r in again.repos] == [("acme/shop", 1)]


async def test_without_a_github_link_the_state_is_empty(index):
    refresher = KnowledgeRefresher(index, snapshot_dir=None)

    state = await pressed(refresher)

    assert (state.last_read_at, state.repos) == (None, [])
    assert state.result is not None and state.result.linked == 0
