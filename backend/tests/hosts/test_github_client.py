import base64
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from bleep.hosts.config import GithubConfig, SecretRef
from bleep.hosts.github import (
    GithubClient,
    GithubError,
    GithubUnavailable,
    NotFound,
    RateLimited,
    TokenRefused,
    github_token,
)
from fakes import PagedCompare, compare_commit

FIXTURES = Path(__file__).parent.parent / "fixtures" / "github"
REPO = "encode/httpx"
SINCE = datetime(2024, 11, 1, tzinfo=UTC)


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def respond(status: int, name: str | None = None, headers: dict[str, str] | None = None):
    return httpx.Response(status, json=fixture(name) if name else None, headers=headers)


class Recorder:
    """A MockTransport handler that answers by path and keeps every request it saw."""

    def __init__(self, routes: dict[str, httpx.Response]) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.routes.get(request.url.path) or respond(404, "not-found.json")


def client(
    routes: dict[str, httpx.Response], token: str | None = None
) -> tuple[GithubClient, Recorder]:
    recorder = Recorder(routes)
    secret = SecretStr(token) if token is not None else None
    return GithubClient(secret, transport=httpx.MockTransport(recorder)), recorder


async def test_commits_parse_the_recorded_listing_and_send_since_and_path():
    github, seen = client({"/repos/encode/httpx/commits": respond(200, "commits.json")})

    commits = await github.commits(REPO, SINCE, path="httpx/_client.py", limit=5)

    assert [c.sha[:7] for c in commits] == ["4189b7f", "15e21e9", "80960fa"]
    assert commits[1].author == "Danarvelini"
    assert commits[1].message == "Updating deprecated docstring Client() class (#3426)"
    assert commits[1].date == datetime(2024, 11, 29, 11, 15, 56, tzinfo=UTC)
    params = seen.requests[0].url.params
    assert params["since"] == "2024-11-01T00:00:00Z"
    assert params["path"] == "httpx/_client.py"
    assert params["per_page"] == "5"


async def test_unauthenticated_by_default_and_the_token_only_as_a_header():
    anonymous, seen = client({"/repos/encode/httpx/commits": respond(200, "commits.json")})
    await anonymous.commits(REPO, SINCE)
    assert "authorization" not in seen.requests[0].headers
    assert seen.requests[0].headers["accept"] == "application/vnd.github+json"

    authed, seen = client({"/repos/encode/httpx/commits": respond(200, "commits.json")}, "ghp_x1")
    await authed.commits(REPO, SINCE)
    assert seen.requests[0].headers["authorization"] == "Bearer ghp_x1"
    assert "ghp_x1" not in repr(authed)


async def test_an_empty_token_counts_as_none():
    github, seen = client({"/repos/encode/httpx/commits": respond(200, "commits.json")}, "")
    await github.commits(REPO, SINCE)
    assert "authorization" not in seen.requests[0].headers
    assert not github.authenticated


async def test_commit_detail_keeps_every_file_and_its_patch():
    sha = "80960fa31918d7663c3f4c3ad61661cf0e80628f"
    github, _ = client({f"/repos/encode/httpx/commits/{sha}": respond(200, "commit-large.json")})

    detail = await github.commit(REPO, sha)

    assert detail.sha == sha
    assert detail.message.startswith("Version 0.28.0.")
    assert len(detail.files) == 6
    assert detail.additions == sum(f.additions for f in detail.files)
    assert all(f.patch for f in detail.files)


async def test_a_missing_repo_is_not_found():
    github, _ = client({})
    with pytest.raises(NotFound):
        await github.commits("encode/no-such-repo", SINCE)


async def test_an_unknown_sha_is_not_found():
    github, _ = client(
        {"/repos/encode/httpx/commits/0000000": respond(422, "commit-not-found.json")}
    )
    with pytest.raises(NotFound):
        await github.commit(REPO, "0000000")


@pytest.mark.parametrize(
    ("status", "headers"),
    [
        (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1793740561"}),
        (429, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1793740561"}),
        (403, {"retry-after": "60"}),
    ],
)
async def test_rate_limits_say_when_they_reset(status, headers):
    github, _ = client(
        {"/repos/encode/httpx/commits": respond(status, "rate-limited.json", headers)}
    )
    with pytest.raises(RateLimited) as caught:
        await github.commits(REPO, SINCE)
    if "x-ratelimit-reset" in headers:
        assert caught.value.reset_at == datetime.fromtimestamp(1793740561, UTC)


async def test_a_403_that_is_not_a_rate_limit_is_a_plain_error():
    github, _ = client(
        {"/repos/encode/httpx/commits": httpx.Response(403, json={"message": "Forbidden"})}
    )
    with pytest.raises(GithubError) as caught:
        await github.commits(REPO, SINCE)
    assert not isinstance(caught.value, RateLimited)


async def test_a_refused_token_is_its_own_error_in_bleeps_words():
    github, _ = client(
        {"/repos/encode/httpx/commits": respond(401, "unauthorized.json")}, token="wrong"
    )
    with pytest.raises(TokenRefused) as caught:
        await github.commits(REPO, SINCE)
    assert "401" in str(caught.value)
    assert "Bad credentials" not in str(caught.value)


async def test_network_errors_and_5xx_are_unavailable():
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    github = GithubClient(None, transport=httpx.MockTransport(down))
    with pytest.raises(GithubUnavailable):
        await github.commits(REPO, SINCE)

    github, _ = client({"/repos/encode/httpx/commits": httpx.Response(502, text="bad gateway")})
    with pytest.raises(GithubUnavailable):
        await github.commits(REPO, SINCE)


async def test_a_body_that_is_not_the_expected_json_is_an_error():
    github, _ = client({"/repos/encode/httpx/commits": httpx.Response(200, text="<html>")})
    with pytest.raises(GithubError):
        await github.commits(REPO, SINCE)
    github, _ = client({"/repos/encode/httpx/commits": httpx.Response(200, json={"a": 1})})
    with pytest.raises(GithubError):
        await github.commits(REPO, SINCE)


@pytest.mark.parametrize("repo", ["httpx", "a/b/c", "../x", "a/b?x=1", "", "a /b"])
async def test_a_malformed_repo_name_never_reaches_github(repo):
    github, seen = client({})
    with pytest.raises(GithubError):
        await github.commits(repo, SINCE)
    assert seen.requests == []


@pytest.mark.parametrize("sha", ["xyz", "123", "../../x", "a" * 41])
async def test_a_malformed_sha_never_reaches_github(sha):
    github, seen = client({})
    with pytest.raises(GithubError):
        await github.commit(REPO, sha)
    assert seen.requests == []


async def test_readme_is_decoded_text():
    github, _ = client({"/repos/encode/httpx/readme": respond(200, "readme.json")})

    readme = await github.readme(REPO)

    expected = base64.b64decode(fixture("readme.json")["content"]).decode()
    assert readme.path == "README.md"
    assert readme.text == expected
    assert readme.url == "https://github.com/encode/httpx/blob/master/README.md"
    assert readme.sha


async def test_list_files_walks_sub_folders_and_keeps_files_only():
    github, seen = client(
        {
            "/repos/encode/httpx/contents/docs": respond(200, "contents-docs.json"),
            "/repos/encode/httpx/contents/docs/advanced": respond(
                200, "contents-docs-advanced.json"
            ),
        }
    )

    files = await github.list_files(REPO, "docs")

    paths = [f.path for f in files]
    assert "docs/api.md" in paths and "docs/advanced/clients.md" in paths
    assert "docs/advanced" not in paths
    assert all(f.type == "file" for f in files)
    # css/, img/ and overrides/ are folders too; each is asked for once and 404s here.
    asked = [r.url.path for r in seen.requests]
    assert asked.count("/repos/encode/httpx/contents/docs/advanced") == 1


async def test_list_files_stops_at_max_files():
    github, _ = client(
        {
            "/repos/encode/httpx/contents/docs": respond(200, "contents-docs.json"),
            "/repos/encode/httpx/contents/docs/advanced": respond(
                200, "contents-docs-advanced.json"
            ),
        }
    )
    assert len(await github.list_files(REPO, "docs", max_files=3)) == 3


async def test_with_suffixes_only_matching_files_are_listed_and_counted():
    images = [{"path": f"docs/img/{n}.png", "type": "file", "size": 1} for n in range(50)]
    pages = [{"path": f"docs/{n}.MD", "type": "file", "size": 1} for n in range(3)]
    github, _ = client(
        {"/repos/encode/httpx/contents/docs": httpx.Response(200, json=images + pages)}
    )

    files = await github.list_files(REPO, "docs", max_files=3, suffixes=(".md", ".markdown"))

    assert [f.path for f in files] == ["docs/0.MD", "docs/1.MD", "docs/2.MD"]
    unfiltered = await github.list_files(REPO, "docs", max_files=3)
    assert [f.path for f in unfiltered] == ["docs/img/0.png", "docs/img/1.png", "docs/img/2.png"]


async def test_a_missing_docs_folder_is_not_found():
    github, _ = client({})
    with pytest.raises(NotFound):
        await github.list_files(REPO, "docs")


async def test_read_file_decodes_and_quotes_the_path():
    github, seen = client(
        {"/repos/encode/httpx/contents/docs/http2.md": respond(200, "contents-docs-http2.json")}
    )

    page = await github.read_file(REPO, "docs/http2.md")

    assert page.path == "docs/http2.md"
    assert page.text.startswith("# HTTP/2")
    with pytest.raises(GithubError):
        await github.read_file(REPO, "docs/../../etc/passwd")
    with pytest.raises(GithubError):
        await github.read_file(REPO, "/etc/passwd")
    assert len(seen.requests) == 1


async def test_read_file_refuses_a_folder():
    github, _ = client({"/repos/encode/httpx/contents/docs": respond(200, "contents-docs.json")})
    with pytest.raises(GithubError):
        await github.read_file(REPO, "docs")


def test_github_token_reads_the_reference(monkeypatch):
    config = GithubConfig(token=SecretRef(env="GITHUB_TOKEN"))
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_abc")
    assert github_token(config).get_secret_value() == "ghp_abc"
    monkeypatch.setenv("GITHUB_TOKEN", "")
    assert github_token(config) is None
    monkeypatch.delenv("GITHUB_TOKEN")
    assert github_token(config) is None
    assert github_token(None) is None


@pytest.mark.parametrize("repo", ["a/..", "a/.", "-a/b"])
async def test_a_dot_repo_name_never_reaches_github(repo):
    github, seen = client({})
    with pytest.raises(GithubError):
        await github.commits(repo, SINCE)
    assert seen.requests == []


@pytest.mark.parametrize(
    ("path", "quoted"),
    [
        ("docs/Getting Started.md", "docs/Getting%20Started.md"),
        ("docs/c#.md", "docs/c%23.md"),
        ("docs/100%.md", "docs/100%25.md"),
        ("docs/why?.md", "docs/why%3F.md"),
    ],
)
async def test_ordinary_file_names_are_read_and_quoted(path, quoted):
    body = fixture("contents-docs-http2.json") | {"path": path}
    github, seen = client({f"/repos/encode/httpx/contents/{path}": httpx.Response(200, json=body)})

    page = await github.read_file(REPO, path)

    assert page.path == path
    assert seen.requests[0].url.raw_path.decode().endswith(f"/contents/{quoted}")


@pytest.mark.parametrize("path", ["docs//a.md", "docs/./a.md", "docs\\a.md", "docs/a\x00.md", ""])
async def test_paths_that_change_the_request_are_refused(path):
    github, seen = client({})
    with pytest.raises(GithubError):
        await github.read_file(REPO, path)
    assert seen.requests == []


async def test_a_secondary_rate_limit_without_headers_is_a_rate_limit():
    body = {"message": "You have exceeded a secondary rate limit. Please wait a few minutes."}
    github, _ = client({"/repos/encode/httpx/commits": httpx.Response(403, json=body)})
    with pytest.raises(RateLimited) as caught:
        await github.commits(REPO, SINCE)
    assert caught.value.reset_at is None
    assert "secondary" not in str(caught.value)  # Bleep's own words, not GitHub's


async def test_retry_after_gives_the_reset_time():
    github, _ = client(
        {
            "/repos/encode/httpx/commits": httpx.Response(
                429, json={}, headers={"retry-after": "120"}
            )
        }
    )
    before = datetime.now(UTC)
    with pytest.raises(RateLimited) as caught:
        await github.commits(REPO, SINCE)
    assert caught.value.reset_at is not None
    assert 115 <= (caught.value.reset_at - before).total_seconds() <= 125


async def test_a_commit_in_a_missing_repo_names_the_repo_and_an_unknown_sha_the_commit():
    github, _ = client({})
    with pytest.raises(NotFound) as missing_repo:
        await github.commit(REPO, "4189b7f")
    assert missing_repo.value.kind == "repo"

    github, _ = client(
        {"/repos/encode/httpx/commits/0000000": respond(422, "commit-not-found.json")}
    )
    with pytest.raises(NotFound) as missing_commit:
        await github.commit(REPO, "0000000")
    assert missing_commit.value.kind == "commit"


async def test_a_renamed_repo_is_followed():
    github, seen = client(
        {
            "/repos/encode/httpx/commits": httpx.Response(
                301, headers={"location": "https://api.github.com/repositories/179492861/commits"}
            ),
            "/repositories/179492861/commits": respond(200, "commits.json"),
        }
    )
    assert len(await github.commits(REPO, SINCE)) == 3
    assert len(seen.requests) == 2


def test_the_hosts_file_refuses_a_repo_that_is_not_owner_and_name():
    from pydantic import ValidationError

    from bleep.hosts.config import ServiceOverrides

    assert ServiceOverrides(repo="DannyKuk/bleep").repo == "DannyKuk/bleep"
    for bad in ["bleep", "DannyKuk/..", "https://github.com/DannyKuk/bleep"]:
        with pytest.raises(ValidationError, match="owner/name"):
            ServiceOverrides(repo=bad)


async def test_a_secondary_limit_waits_for_retry_after_not_the_primary_reset():
    headers = {
        "retry-after": "60",
        "x-ratelimit-remaining": "4000",
        "x-ratelimit-reset": "1793740561",
    }
    github, _ = client(
        {"/repos/encode/httpx/commits": httpx.Response(403, json={}, headers=headers)}
    )
    before = datetime.now(UTC)
    with pytest.raises(RateLimited) as caught:
        await github.commits(REPO, SINCE)
    assert caught.value.reset_at is not None
    assert 55 <= (caught.value.reset_at - before).total_seconds() <= 65


BASE, HEAD = "a" * 40, "b" * 40


def paged(count: int, **kwargs) -> tuple[GithubClient, PagedCompare]:
    compare = PagedCompare([compare_commit(i) for i in range(count)], **kwargs)
    return GithubClient(None, transport=httpx.MockTransport(compare)), compare


async def indexes(github: GithubClient, skip: int, count: int = 100) -> list[int]:
    found = await github.compare_older(REPO, BASE, HEAD, skip, count)
    return [int(c.sha[:7], 16) for c in found.commits]


async def test_compare_older_reads_the_commits_after_the_newest_skip_newest_first():
    github, _ = paged(1371)  # as long as the range the fake was measured on

    assert await indexes(github, 0, 3) == [1370, 1369, 1368]
    assert await indexes(github, 1300) == list(range(70, -1, -1))  # ends at GitHub's first
    for skip in (0, 85, 500, 1271):
        part = await indexes(github, skip)
        assert part == list(range(1370 - skip, 1370 - skip - len(part), -1))  # right after skip
        assert 80 <= len(part) <= 100  # one page holds most of what was asked for


async def test_compare_older_walks_a_long_range_in_parts_without_gap_or_repeat():
    github, _ = paged(1371)
    seen: list[int] = []

    while part := await indexes(github, len(seen), 37):
        seen += part

    assert seen == list(range(1370, -1, -1))


def asked(compare: PagedCompare) -> list[tuple[str, str]]:
    return [(r.url.params["page"], r.url.params["per_page"]) for r in compare.requests]


async def test_a_cold_compare_older_asks_the_total_without_the_file_list_then_one_page():
    github, compare = paged(1371)

    found = await github.compare_older(REPO, BASE, HEAD, 85)

    assert found.total_commits == 1371 and found.status == "ahead"
    # The total from a page past the first (page 1 carries the whole file list), then the one
    # page, 13 of 99, that holds positions 1188 to 1285 (counted from the oldest).
    assert asked(compare) == [("2", "1"), ("13", "99")]


async def test_the_total_is_asked_once_for_the_same_shas():
    github, compare = paged(1371)

    await github.compare_older(REPO, BASE, HEAD, 0)
    await github.compare_older(REPO, BASE, HEAD, 97)
    await github.compare_older(REPO, BASE, HEAD, 195)

    assert [page for page, _ in asked(compare)].count("2") == 1  # the probe, the first time
    assert len(compare.requests) == 4


async def test_the_unpaged_compare_tells_the_total_so_the_next_part_is_one_request():
    github, compare = paged(1371)
    await github.compare(REPO, BASE, HEAD)
    compare.requests.clear()

    await github.compare_older(REPO, BASE, HEAD, 85)

    assert asked(compare) == [("13", "99")]


async def test_when_no_one_page_holds_most_of_a_part_two_pages_of_100_are_read():
    github, compare = paged(150)

    part = await indexes(github, 0)

    assert part == list(range(149, 49, -1))  # 100 commits, from the oldest page boundary
    assert asked(compare) == [("2", "1"), ("1", "100"), ("2", "100")]


async def test_compare_older_reads_the_oldest_part_from_page_one():
    github, compare = paged(1371)

    await github.compare_older(REPO, BASE, HEAD, 1300)

    assert asked(compare) == [("2", "1"), ("1", "71")]


async def test_compare_older_past_the_oldest_is_empty_with_the_total():
    github, compare = paged(40)

    found = await github.compare_older(REPO, BASE, HEAD, 40)

    assert found.commits == [] and found.total_commits == 40
    assert len(compare.requests) == 1


async def test_compare_older_of_a_rollback_reads_no_commits():
    github, compare = paged(0, status="behind", behind_by=3)

    found = await github.compare_older(REPO, BASE, HEAD, 5)

    assert found.status == "behind" and found.commits == [] and len(compare.requests) == 1


async def test_the_unpaged_compare_holds_the_newest_250_newest_first():
    github, compare = paged(1371)

    found = await github.compare(REPO, BASE, HEAD)

    assert [int(c.sha[:7], 16) for c in found.commits] == list(range(1370, 1120, -1))
    assert found.total_commits == 1371
    assert "page" not in compare.requests[0].url.params
