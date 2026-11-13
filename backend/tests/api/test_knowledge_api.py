"""Reading the linked repos' docs now, over HTTP (#556)."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from bleep.knowledge.ingest import KnowledgeRefresher, RepoDocFiles
from bleep.main import create_app
from bleep.schemas.github import RepoFile


def readme(repo: str, text: str = "# Shop\n\nRun `make db` before the web.") -> RepoFile:
    return RepoFile(path="README.md", sha="0" * 40, url=f"https://github.com/{repo}", text=text)


class Repos:
    """Linked repos and their README; a repo in `failing` raises like an unreachable API."""

    def __init__(self, files: dict[str, list[RepoFile]]) -> None:
        self.files = files
        self.failing: set[str] = set()
        self.asked: list[str] = []

    async def linked_repos(self) -> list[str]:
        return list(self.files)

    async def doc_files(self, repo: str) -> RepoDocFiles:
        self.asked.append(repo)
        if repo in self.failing:
            raise RuntimeError("GitHub answered 503")
        return RepoDocFiles(files=self.files[repo])


@pytest.fixture
def repos() -> Repos:
    return Repos({"acme/shop": [readme("acme/shop")], "acme/blog": [readme("acme/blog")]})


@pytest.fixture
def docs_client(settings, parts, repos, monkeypatch):
    """A logged-in client whose refresher reads `repos`; the daily loop stays off, so only a
    request reads them."""
    parts.knowledge_refresher = KnowledgeRefresher(parts.knowledge, repos=repos, snapshot_dir=None)

    async def idle() -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(parts.knowledge_refresher, "run", idle)
    with TestClient(create_app(settings, parts)) as client:
        assert client.post("/api/auth/login", json={"password": "test-password"}).status_code == 204
        yield client


def test_the_state_before_any_read_lists_the_linked_repos_with_no_chunks(docs_client):
    body = docs_client.get("/api/knowledge/repos").json()

    assert body == {
        "last_read_at": None,
        "repos": [{"repo": "acme/blog", "chunks": 0}, {"repo": "acme/shop", "chunks": 0}],
    }


def test_a_press_reads_the_repos_and_answers_with_the_chunks_and_the_time(docs_client, repos):
    response = docs_client.post("/api/knowledge/repos/refresh")

    assert response.status_code == 200
    body = response.json()
    assert (body["read"], body["linked"], body["embedded"]) == (2, 2, 2)
    assert body["repos"] == [{"repo": "acme/blog", "chunks": 1}, {"repo": "acme/shop", "chunks": 1}]
    assert body["last_read_at"] is not None
    assert sorted(repos.asked) == ["acme/blog", "acme/shop"]
    state = docs_client.get("/api/knowledge/repos").json()
    assert state == {"last_read_at": body["last_read_at"], "repos": body["repos"]}


def test_a_press_after_the_docs_changed_embeds_the_new_text(docs_client, repos):
    docs_client.post("/api/knowledge/repos/refresh")
    repos.files = {"acme/shop": [readme("acme/shop", "# Shop\n\nSet HOST to the proxy name.")]}

    body = docs_client.post("/api/knowledge/repos/refresh").json()

    assert (body["read"], body["linked"], body["embedded"]) == (1, 1, 1)
    assert body["repos"] == [{"repo": "acme/shop", "chunks": 1}]  # the unlinked blog is gone


def test_a_press_that_reaches_no_repo_says_so_and_keeps_what_was_read(docs_client, repos):
    first = docs_client.post("/api/knowledge/repos/refresh").json()
    repos.failing = {"acme/shop", "acme/blog"}

    body = docs_client.post("/api/knowledge/repos/refresh").json()

    assert (body["read"], body["linked"], body["embedded"]) == (0, 2, 0)
    assert body["last_read_at"] == first["last_read_at"]
    assert body["repos"] == first["repos"]


def test_both_routes_need_a_session(settings, parts):
    with TestClient(create_app(settings, parts)) as client:
        assert client.get("/api/knowledge/repos").status_code == 401
        assert client.post("/api/knowledge/repos/refresh").status_code == 401
