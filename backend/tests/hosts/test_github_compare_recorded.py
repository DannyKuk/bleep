"""`GithubClient.compare_older` against answers recorded from GitHub's compare of a real range of
1,371 commits (see tests/fixtures/github/README.md), through `httpx.MockTransport`: the paging
the client relies on is checked on real data, offline."""

import json
from pathlib import Path

import httpx

from bleep.hosts.github import GithubClient

FIXTURES = Path(__file__).parent.parent / "fixtures" / "github"
REPO = "expressjs/express"
BASE = "147c2507c3bdcd22c7c0176e57c9d585d0aa2642"  # the commit of the tag 4.0.0
HEAD = "547fdd41dca9ae9c49956748cc0bd1f011310fb6"  # the commit of the tag 4.18.0
TOTAL = 1371
ANSWERS = {  # (page, per_page) -> the recorded answer; None is the unpaged one
    None: "compare-express-unpaged.json",
    (2, 1): "compare-express-page2-per1.json",
    (13, 99): "compare-express-page13-per99.json",
    (14, 98): "compare-express-page14-per98.json",
    (1, 71): "compare-express-page1-per71.json",
    (15, 100): "compare-express-page15-per100.json",
}


def recorded(key: tuple[int, int] | None) -> dict:
    return json.loads((FIXTURES / ANSWERS[key]).read_text(encoding="utf-8"))


def shas(key: tuple[int, int] | None) -> list[str]:
    return [c["sha"] for c in recorded(key)["commits"]]


class RecordedGitHub:
    """Answers the compare with the recorded answer of the same paging; any other request
    fails the test, as the recording holds nothing for it."""

    def __init__(self) -> None:
        self.asked: list[tuple[int, int] | None] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        key = (int(params["page"]), int(params["per_page"])) if "page" in params else None
        assert key in ANSWERS, f"no recorded answer for {request.url.query!r}"
        self.asked.append(key)
        return httpx.Response(200, json=recorded(key))


def client() -> tuple[GithubClient, RecordedGitHub]:
    answers = RecordedGitHub()
    return GithubClient(None, transport=httpx.MockTransport(answers)), answers


def test_the_recorded_pages_are_chronological_from_the_oldest_and_the_unpaged_holds_the_newest():
    unpaged, page13, page14 = shas(None), shas((13, 99)), shas((14, 98))

    assert len(unpaged) == 250 and len(page13) == 99 and len(page14) == 97
    assert page14 == unpaged[-97:]  # page 14 of 98 ends at the newest commit, as the unpaged does
    # Page 13 of 99 holds positions 1188 to 1286 and page 14 of 98 those from 1274: 13 shared.
    assert page13[1274 - 1188 :] == page14[:13]
    assert len(shas((1, 71))) == 71  # page 1 of 71 holds the oldest 71, none of the others
    assert not set(shas((1, 71))) & set(unpaged + page13)


def test_every_recorded_page_says_the_size_of_the_whole_range_and_only_the_first_lists_files():
    for key in ANSWERS:
        answer = recorded(key)
        assert answer["total_commits"] == answer["ahead_by"] == TOTAL
        assert answer["status"] == "ahead" and answer["behind_by"] == 0
        assert ("files" in answer) == (key in (None, (1, 71)))


def test_a_page_past_the_end_is_an_answer_with_no_commits_and_the_total():
    answer = recorded((15, 100))

    assert answer["commits"] == [] and answer["total_commits"] == TOTAL


async def test_the_unpaged_compare_reads_the_newest_250_newest_first():
    github, _ = client()

    found = await github.compare(REPO, BASE, HEAD)

    assert [c.sha for c in found.commits] == shas(None)[::-1]
    assert found.total_commits == TOTAL


async def test_a_part_after_the_unpaged_compare_is_one_request_for_the_page_that_holds_it():
    github, answers = client()
    await github.compare(REPO, BASE, HEAD)

    found = await github.compare_older(REPO, BASE, HEAD, 85)

    assert answers.asked == [None, (13, 99)]  # the total was learnt from the unpaged answer
    assert [c.sha for c in found.commits] == shas((13, 99))[:98][::-1]
    assert found.total_commits == TOTAL and found.status == "ahead"


async def test_the_newest_part_is_the_last_page_and_ends_at_the_newest_commit():
    github, answers = client()
    await github.compare(REPO, BASE, HEAD)

    found = await github.compare_older(REPO, BASE, HEAD, 0)

    assert answers.asked == [None, (14, 98)]
    assert [c.sha for c in found.commits] == shas(None)[-97:][::-1]


async def test_a_cold_client_asks_the_total_with_a_page_of_one_then_the_oldest_part_from_page_one():
    github, answers = client()

    found = await github.compare_older(REPO, BASE, HEAD, 1300)

    assert answers.asked == [(2, 1), (1, 71)]  # page 2: no file list; page 1 carries it
    assert [c.sha for c in found.commits] == shas((1, 71))[::-1]
    assert found.commits[-1].sha == shas((1, 71))[0]  # the oldest commit of the range, last


async def test_past_the_oldest_commit_asks_for_no_page():
    github, answers = client()
    await github.compare(REPO, BASE, HEAD)

    found = await github.compare_older(REPO, BASE, HEAD, TOTAL)

    assert found.commits == [] and found.total_commits == TOTAL
    assert answers.asked == [None]
