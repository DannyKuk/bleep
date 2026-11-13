"""Reading the linked repos' docs now, over HTTP (#556)."""

import asyncio
import time

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
        self.gate: asyncio.Event | None = None  # when set, a read waits until it is

    async def linked_repos(self) -> list[str]:
        return list(self.files)

    async def doc_files(self, repo: str) -> RepoDocFiles:
        self.asked.append(repo)
        if self.gate is not None:
            await self.gate.wait()
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


def settled(client: TestClient) -> dict:
    """The state once the background read is over."""
    for _ in range(200):
        body = client.get("/api/knowledge/repos").json()
        if not body["reading"]:
            return body
        time.sleep(0.02)
    raise AssertionError("the read did not end")


def test_the_state_before_any_read_lists_the_linked_repos_with_no_chunks(docs_client):
    body = docs_client.get("/api/knowledge/repos").json()

    assert body == {
        "last_read_at": None,
        "repos": [{"repo": "acme/blog", "chunks": 0}, {"repo": "acme/shop", "chunks": 0}],
        "reading": False,
        "result": None,
    }


def test_a_press_answers_at_once_and_the_state_follows_the_read_to_its_result(docs_client, repos):
    repos.gate = asyncio.Event()

    response = docs_client.post("/api/knowledge/repos/refresh")

    assert response.status_code == 202
    assert response.json()["reading"] is True
    assert docs_client.get("/api/knowledge/repos").json()["reading"] is True  # still reading
    docs_client.portal.call(repos.gate.set)

    body = settled(docs_client)
    assert body["result"] == {"failed": False, "read": 2, "linked": 2, "embedded": 2}
    assert body["repos"] == [{"repo": "acme/blog", "chunks": 1}, {"repo": "acme/shop", "chunks": 1}]
    assert body["last_read_at"] is not None
    assert sorted(repos.asked) == ["acme/blog", "acme/shop"]


def test_a_second_press_while_reading_does_not_start_another_read(docs_client, repos):
    repos.gate = asyncio.Event()

    first = docs_client.post("/api/knowledge/repos/refresh")
    second = docs_client.post("/api/knowledge/repos/refresh")
    docs_client.portal.call(repos.gate.set)
    settled(docs_client)

    assert (first.status_code, second.status_code) == (202, 202)
    assert second.json()["reading"] is True
    assert sorted(repos.asked) == ["acme/blog", "acme/shop"]  # each repo once


def test_a_press_after_the_docs_changed_embeds_the_new_text(docs_client, repos):
    docs_client.post("/api/knowledge/repos/refresh")
    settled(docs_client)
    repos.files = {"acme/shop": [readme("acme/shop", "# Shop\n\nSet HOST to the proxy name.")]}

    docs_client.post("/api/knowledge/repos/refresh")
    body = settled(docs_client)

    assert body["result"] == {"failed": False, "read": 1, "linked": 1, "embedded": 1}
    assert body["repos"] == [{"repo": "acme/shop", "chunks": 1}]  # the unlinked blog is gone


def test_a_press_that_reaches_no_repo_says_so_and_keeps_what_was_read(docs_client, repos):
    docs_client.post("/api/knowledge/repos/refresh")
    first = settled(docs_client)
    repos.failing = {"acme/shop", "acme/blog"}

    docs_client.post("/api/knowledge/repos/refresh")
    body = settled(docs_client)

    assert body["result"] == {"failed": False, "read": 0, "linked": 2, "embedded": 0}
    assert body["last_read_at"] == first["last_read_at"]
    assert body["repos"] == first["repos"]


def test_a_read_that_fails_is_reported_as_failed_without_the_error(docs_client, parts, caplog):
    def broken(texts):
        raise ConnectionError("the embedder at http://10.0.0.7:8080 is down")

    parts.knowledge.embedder.embed = broken  # type: ignore[method-assign]

    with caplog.at_level("WARNING", logger="bleep.knowledge.ingest"):
        assert docs_client.post("/api/knowledge/repos/refresh").status_code == 202
        response = docs_client.get("/api/knowledge/repos")
        body = settled(docs_client)

    assert response.status_code == 200
    assert body["result"] == {"failed": True, "read": 0, "linked": 0, "embedded": 0}
    assert "10.0.0.7" not in str(body)
    assert "the embedder at http://10.0.0.7:8080 is down" in caplog.text  # the log has the why


def test_both_routes_need_a_session(settings, parts):
    with TestClient(create_app(settings, parts)) as client:
        assert client.get("/api/knowledge/repos").status_code == 401
        assert client.post("/api/knowledge/repos/refresh").status_code == 401
