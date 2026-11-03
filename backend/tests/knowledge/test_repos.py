import json
from pathlib import Path

import httpx
import pytest

from bleep.hosts.config import ServiceOverrides
from bleep.hosts.github import GithubClient, RateLimited
from bleep.knowledge.index import KnowledgeIndex
from bleep.knowledge.ingest import KnowledgeRefresher
from bleep.knowledge.repos import GithubRepoDocs
from bleep.memory.embeddings import FakeEmbedder
from bleep.schemas.services import HostState
from bleep.store.db import Database
from fakes import socket_host

FIXTURES = Path(__file__).parent.parent / "fixtures" / "github"
REPO = "encode/httpx"
RATE_LIMITED = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1793740561"}


def recorded(name: str, status: int = 200, headers: dict[str, str] | None = None):
    body = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return httpx.Response(status, json=body, headers=headers)


class GitHub:
    """Answers by path from the recorded fixtures; anything else is GitHub's 404."""

    def __init__(self, routes: dict[str, httpx.Response]) -> None:
        self.routes = routes
        self.asked: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(request.url.path)
        return self.routes.get(request.url.path) or recorded("not-found.json", 404)

    def client(self) -> GithubClient:
        return GithubClient(None, transport=httpx.MockTransport(self))


HTTPX = {
    "/repos/encode/httpx/readme": recorded("readme.json"),
    "/repos/encode/httpx/contents/docs": recorded("contents-docs.json"),
    "/repos/encode/httpx/contents/docs/advanced": recorded("contents-docs-advanced.json"),
    "/repos/encode/httpx/contents/docs/http2.md": recorded("contents-docs-http2.json"),
}


class Hosts:
    """The registry as the adapter sees it: the hosts file, before any host was polled."""

    def __init__(self, services: dict[str, ServiceOverrides]) -> None:
        self.state = HostState(
            config=socket_host(services=services), reachable=False, last_seen=None
        )

    def hosts(self) -> list[HostState]:
        return [self.state]


def linked(*services: ServiceOverrides) -> Hosts:
    return Hosts({f"svc{n}": s for n, s in enumerate(services)})


async def test_linked_repos_come_from_the_hosts_file_before_any_discovery():
    hosts = linked(
        ServiceOverrides(repo=REPO),
        ServiceOverrides(repo="acme/blog"),
        ServiceOverrides(repo=REPO, docs_paths=["runbooks"]),
        ServiceOverrides(),
    )

    assert await GithubRepoDocs(GitHub({}).client(), hosts).linked_repos() == [
        "acme/blog",
        REPO,
    ]


async def test_a_repo_gives_its_readme_then_the_markdown_under_docs():
    github = GitHub(HTTPX)

    files = await GithubRepoDocs(github.client(), linked(ServiceOverrides(repo=REPO))).doc_files(
        REPO
    )

    assert [f.path for f in files] == ["README.md", "docs/http2.md"]  # the rest answered 404
    assert files[0].text.startswith('<p align="center">')
    assert "HTTP/2" in files[1].text
    assert "/repos/encode/httpx/contents/docs/CNAME" not in github.asked  # not markdown
    assert "/repos/encode/httpx/contents/docs/advanced/ssl.md" in github.asked  # sub-folders too


async def test_named_doc_paths_replace_docs_and_a_markdown_path_is_read_as_a_file():
    github = GitHub(HTTPX)
    hosts = linked(ServiceOverrides(repo=REPO, docs_paths=["docs/http2.md", "runbooks"]))

    files = await GithubRepoDocs(github.client(), hosts).doc_files(REPO)

    assert [f.path for f in files] == ["README.md", "docs/http2.md"]
    assert "/repos/encode/httpx/contents/docs" not in github.asked
    assert "/repos/encode/httpx/contents/runbooks" in github.asked  # missing: skipped


async def test_a_repo_without_readme_or_docs_has_no_files():
    files = await GithubRepoDocs(
        GitHub({}).client(), linked(ServiceOverrides(repo=REPO))
    ).doc_files(REPO)

    assert files == []


async def test_a_rate_limit_is_raised_and_stops_the_daily_refresh(tmp_path):
    github = GitHub(
        {"/repos/encode/httpx/readme": recorded("rate-limited.json", 403, RATE_LIMITED)}
    )
    hosts = linked(ServiceOverrides(repo=REPO), ServiceOverrides(repo="encode/starlette"))
    docs = GithubRepoDocs(github.client(), hosts)
    with pytest.raises(RateLimited):
        await docs.doc_files(REPO)

    db = Database(tmp_path / "bleep.db")
    await db.migrate()
    github.asked.clear()
    assert (
        await KnowledgeRefresher(KnowledgeIndex(db, FakeEmbedder()), repos=docs).refresh_repos()
        == 0
    )
    assert github.asked == ["/repos/encode/httpx/readme"]  # starlette was not tried


async def test_the_refresher_indexes_a_repos_docs_with_their_github_links(tmp_path):
    db = Database(tmp_path / "bleep.db")
    await db.migrate()
    index = KnowledgeIndex(db, FakeEmbedder())
    docs = GithubRepoDocs(GitHub(HTTPX).client(), linked(ServiceOverrides(repo=REPO)))

    assert await KnowledgeRefresher(index, repos=docs, snapshot_dir=None).refresh_repos() == 2

    assert await index.document_ids("repo:") == {
        "repo:encode/httpx:README.md",
        "repo:encode/httpx:docs/http2.md",
    }
    [hit] = await index.search("HTTP/2 support", k=1, sources={"repo"})
    assert hit.url.startswith("https://github.com/encode/httpx/blob/")


async def test_a_doc_path_that_is_a_file_but_not_markdown_is_skipped_not_fatal():
    routes = HTTPX | {
        "/repos/encode/httpx/contents/compose.yaml": recorded("contents-docs-http2.json")
    }
    hosts = linked(ServiceOverrides(repo=REPO, docs_paths=["compose.yaml", "docs"]))

    files = await GithubRepoDocs(GitHub(routes).client(), hosts).doc_files(REPO)

    assert [f.path for f in files] == ["README.md", "docs/http2.md"]


async def test_an_outage_while_listing_is_raised_so_the_repo_keeps_its_docs():
    from bleep.hosts.github import GithubUnavailable

    routes = HTTPX | {"/repos/encode/httpx/contents/docs": httpx.Response(503)}
    docs = GithubRepoDocs(GitHub(routes).client(), linked(ServiceOverrides(repo=REPO)))

    with pytest.raises(GithubUnavailable):
        await docs.doc_files(REPO)
