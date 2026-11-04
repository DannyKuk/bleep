import base64
import json
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest

from bleep.hosts.config import HostConfig, ServiceOverrides, load_hosts_file
from bleep.hosts.github import GithubClient, RateLimited
from bleep.knowledge.index import KnowledgeIndex
from bleep.knowledge.ingest import MAX_REPO_FILES, KnowledgeRefresher, RepoDocFiles, RepoRefresh
from bleep.knowledge.repos import GithubRepoDocs
from bleep.memory.embeddings import FakeEmbedder
from bleep.schemas.services import HostState
from bleep.store.db import Database
from fakes import socket_host

FIXTURES = Path(__file__).parent.parent / "fixtures" / "github"
CHECKOUT = Path(__file__).resolve().parents[3]
DEMO_HOSTS = CHECKOUT / "config" / "hosts.demo.yaml"
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

    def __init__(self, services: dict[str, ServiceOverrides], config: HostConfig | None = None):
        config = config or socket_host(services=services)
        self.state = HostState(config=config, reachable=False, last_seen=None)

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

    docs = GithubRepoDocs(github.client(), linked(ServiceOverrides(repo=REPO)))

    files = (await docs.doc_files(REPO)).files

    assert [f.path for f in files] == ["README.md", "docs/http2.md"]  # the rest answered 404
    assert files[0].text.startswith('<p align="center">')
    assert "HTTP/2" in files[1].text
    assert "/repos/encode/httpx/contents/docs/CNAME" not in github.asked  # not markdown
    assert "/repos/encode/httpx/contents/docs/advanced/ssl.md" in github.asked  # sub-folders too


async def test_named_doc_paths_replace_docs_and_a_markdown_path_is_read_as_a_file():
    github = GitHub(HTTPX)
    hosts = linked(ServiceOverrides(repo=REPO, docs_paths=["docs/http2.md", "runbooks"]))

    files = (await GithubRepoDocs(github.client(), hosts).doc_files(REPO)).files

    assert [f.path for f in files] == ["README.md", "docs/http2.md"]
    assert "/repos/encode/httpx/contents/docs" not in github.asked
    assert "/repos/encode/httpx/contents/runbooks" in github.asked  # missing: skipped


async def test_a_repo_without_readme_or_docs_has_no_files():
    docs = GithubRepoDocs(GitHub({}).client(), linked(ServiceOverrides(repo=REPO)))

    assert await docs.doc_files(REPO) == RepoDocFiles(files=[])


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
    refresher = KnowledgeRefresher(KnowledgeIndex(db, FakeEmbedder()), repos=docs)
    assert await refresher.refresh_repos() == RepoRefresh(embedded=0, read=0, linked=2)
    assert github.asked == ["/repos/encode/httpx/readme"]  # starlette was not tried


async def test_the_refresher_indexes_a_repos_docs_with_their_github_links(tmp_path):
    db = Database(tmp_path / "bleep.db")
    await db.migrate()
    index = KnowledgeIndex(db, FakeEmbedder())
    docs = GithubRepoDocs(GitHub(HTTPX).client(), linked(ServiceOverrides(repo=REPO)))

    refresher = KnowledgeRefresher(index, repos=docs, snapshot_dir=None)
    assert (await refresher.refresh_repos()).embedded == 2

    assert await index.document_ids("repo:") == {
        "repo:encode/httpx:README.md",
        "repo:encode/httpx:docs/http2.md",
    }
    [hit] = await index.search("HTTP/2 support", k=1, sources={"repo"})
    assert hit.url.startswith("https://github.com/encode/httpx/blob/")


async def test_a_doc_path_that_is_a_file_but_not_markdown_is_skipped_not_fatal():
    github = GitHub(HTTPX)
    hosts = linked(ServiceOverrides(repo=REPO, docs_paths=["compose.yaml", "docs"]))

    files = (await GithubRepoDocs(github.client(), hosts).doc_files(REPO)).files

    assert [f.path for f in files] == ["README.md", "docs/http2.md"]
    assert "/repos/encode/httpx/contents/compose.yaml" not in github.asked


async def test_an_outage_while_listing_is_raised_so_the_repo_keeps_its_docs():
    from bleep.hosts.github import GithubUnavailable

    routes = HTTPX | {"/repos/encode/httpx/contents/docs": httpx.Response(503)}
    docs = GithubRepoDocs(GitHub(routes).client(), linked(ServiceOverrides(repo=REPO)))

    with pytest.raises(GithubUnavailable):
        await docs.doc_files(REPO)


async def test_a_file_too_large_to_read_is_skipped_and_the_rest_are_kept():
    too_large = json.loads((FIXTURES / "contents-docs-http2.json").read_text(encoding="utf-8"))
    too_large |= {"path": "docs/api.md", "encoding": "none", "content": None}
    routes = HTTPX | {
        "/repos/encode/httpx/contents/docs/api.md": httpx.Response(200, json=too_large)
    }
    hosts = linked(ServiceOverrides(repo=REPO))

    files = (await GithubRepoDocs(GitHub(routes).client(), hosts).doc_files(REPO)).files

    assert [f.path for f in files] == ["README.md", "docs/http2.md"]


async def test_a_file_that_fails_to_read_keeps_its_stored_doc(tmp_path):
    db = Database(tmp_path / "bleep.db")
    await db.migrate()
    index = KnowledgeIndex(db, FakeEmbedder())
    hosts = linked(ServiceOverrides(repo=REPO))
    await KnowledgeRefresher(
        index, repos=GithubRepoDocs(GitHub(HTTPX).client(), hosts), snapshot_dir=None
    ).refresh_repos()
    forbidden = httpx.Response(403, json={"message": "Resource not accessible"})
    routes = HTTPX | {"/repos/encode/httpx/contents/docs/http2.md": forbidden}
    docs = GithubRepoDocs(GitHub(routes).client(), hosts)

    read = await docs.doc_files(REPO)
    await KnowledgeRefresher(index, repos=docs, snapshot_dir=None).refresh_repos()

    assert ([f.path for f in read.files], read.unread) == (["README.md"], ["docs/http2.md"])
    assert await index.document_ids("repo:") == {
        "repo:encode/httpx:README.md",
        "repo:encode/httpx:docs/http2.md",
    }
    [hit] = await index.search("HTTP/2 support", k=1, sources={"repo"})
    assert hit.url.endswith("docs/http2.md")


async def test_a_service_can_leave_the_readme_out_unless_another_wants_it():
    github = GitHub(HTTPX)
    without = linked(ServiceOverrides(repo=REPO, readme=False))

    files = (await GithubRepoDocs(github.client(), without).doc_files(REPO)).files

    assert [f.path for f in files] == ["docs/http2.md"]
    assert "/repos/encode/httpx/readme" not in github.asked
    both = linked(ServiceOverrides(repo=REPO, readme=False), ServiceOverrides(repo=REPO))
    files = (await GithubRepoDocs(github.client(), both).doc_files(REPO)).files
    assert [f.path for f in files] == ["README.md", "docs/http2.md"]


async def test_the_file_cap_counts_markdown_only():
    images = [{"path": f"docs/img/{n}.png", "type": "file"} for n in range(MAX_REPO_FILES + 5)]
    page = {"path": "docs/runbook.md", "type": "file"}
    runbook = recorded("contents-docs-http2.json").json() | {"path": "docs/runbook.md"}
    routes = {
        "/repos/encode/httpx/contents/docs": httpx.Response(200, json=[*images, page]),
        "/repos/encode/httpx/contents/docs/runbook.md": httpx.Response(200, json=runbook),
    }
    docs = GithubRepoDocs(GitHub(routes).client(), linked(ServiceOverrides(repo=REPO)))

    assert [f.path for f in (await docs.doc_files(REPO)).files] == ["docs/runbook.md"]


class Checkout:
    """GitHub serving this checkout as the repo `DannyKuk/bleep`: its README, its folders and
    its files, as the contents API would."""

    PREFIX = "/repos/DannyKuk/bleep"

    def __init__(self) -> None:
        self.asked: list[str] = []

    def _file(self, path: str) -> dict:
        data = (CHECKOUT / path).read_bytes()
        return {
            "path": path,
            "type": "file",
            "sha": "0" * 40,
            "size": len(data),
            "encoding": "base64",
            "content": base64.b64encode(data).decode(),
            "html_url": f"https://github.com/DannyKuk/bleep/blob/main/{path}",
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.path)
        self.asked.append(path)
        if path == f"{self.PREFIX}/readme":
            return httpx.Response(200, json=self._file("README.md"))
        local = path.removeprefix(f"{self.PREFIX}/contents/")
        if path.startswith(f"{self.PREFIX}/contents/") and (CHECKOUT / local).is_dir():
            entries = sorted((CHECKOUT / local).iterdir())
            return httpx.Response(
                200,
                json=[
                    {"path": f"{local}/{e.name}", "type": "dir" if e.is_dir() else "file"}
                    for e in entries
                ],
            )
        if path.startswith(f"{self.PREFIX}/contents/") and (CHECKOUT / local).is_file():
            return httpx.Response(200, json=self._file(local))
        return recorded("not-found.json", 404)


async def test_the_demo_reads_the_shops_own_docs_and_none_of_bleeps(tmp_path):
    db = Database(tmp_path / "bleep.db")
    await db.migrate()
    index = KnowledgeIndex(db, FakeEmbedder())
    github = Checkout()
    demo = Hosts({}, config=load_hosts_file(DEMO_HOSTS).hosts[0])
    docs = GithubRepoDocs(GithubClient(None, transport=httpx.MockTransport(github)), demo)

    await KnowledgeRefresher(index, repos=docs, snapshot_dir=None).refresh_repos()

    ids = await index.document_ids("repo:")
    assert ids == {
        "repo:DannyKuk/bleep:demo/shop/docs/deploy.md",
        "repo:DannyKuk/bleep:demo/shop/docs/services.md",
    }
    assert all(not path.startswith("/repos/DannyKuk/bleep/contents/docs") for path in github.asked)
    assert "/repos/DannyKuk/bleep/readme" not in github.asked


async def test_the_checkout_fake_serves_bleeps_docs_to_a_service_that_names_none():
    github = Checkout()
    default = linked(ServiceOverrides(repo="DannyKuk/bleep"))
    docs = GithubRepoDocs(GithubClient(None, transport=httpx.MockTransport(github)), default)

    paths = [f.path for f in (await docs.doc_files("DannyKuk/bleep")).files]

    assert paths[0] == "README.md"
    assert "docs/demo.md" in paths  # what the demo read before it named the shop's docs
