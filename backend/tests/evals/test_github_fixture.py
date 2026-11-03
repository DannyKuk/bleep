import json
from datetime import UTC, datetime, timedelta

import pytest

from bleep.hosts.github import NotFound
from bleep.tools.github import describe_commits, describe_diff
from evals.github import (
    FIXTURES_DIR,
    FixtureCommit,
    FixtureFile,
    GithubFixture,
    GithubFixtureError,
    fixture_client,
    load_github_fixture,
)
from evals.injection import load_cases
from evals.scenarios import ScenarioError, load_scenario, load_scenarios

START = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
REPO = "DannyKuk/bleep"


def commit(key: str, minutes_ago: int, files: list[str]) -> FixtureCommit:
    return FixtureCommit(
        sha=key * 40,
        minutes_ago=minutes_ago,
        author="dev",
        message=f"commit {key}",
        files=[FixtureFile(filename=f, patch="@@ -1 +1 @@\n-a\n+b") for f in files],
    )


FIXTURE = GithubFixture(
    commits=[
        commit("a", 10, ["demo/shop/app.py"]),
        commit("b", 120, ["demo/compose.yaml"]),
        commit("c", 60 * 72, ["README.md"]),
    ]
)


async def test_the_list_is_dated_from_the_start_and_filtered_by_since_and_path():
    github = fixture_client(FIXTURE, lambda: START)

    recent = await github.commits(REPO, START - timedelta(hours=48))
    shop = await github.commits(REPO, START - timedelta(hours=48), path="demo/shop")
    one = await github.commits(REPO, START - timedelta(hours=48), limit=1)

    assert [c.message for c in recent] == ["commit a", "commit b"]
    assert recent[0].date == START - timedelta(minutes=10)
    assert [c.message for c in shop] == ["commit a"]
    assert [c.message for c in one] == ["commit a"]


async def test_a_commit_is_found_by_a_sha_prefix_and_an_unknown_one_is_not_found():
    github = fixture_client(FIXTURE, lambda: START)

    detail = await github.commit(REPO, "bbbbbbb")

    assert detail.files[0].filename == "demo/compose.yaml"
    assert detail.additions == 0 and detail.url == f"https://github.com/{REPO}/commit/{'b' * 40}"
    with pytest.raises(NotFound):
        await github.commit(REPO, "1234567")
    with pytest.raises(NotFound):
        await github.readme(REPO)


def test_a_planted_commit_is_the_newest():
    planted = commit("d", 30, ["demo/shop/app.py"])
    assert [c.sha[0] for c in FIXTURE.with_newest(planted).commits] == ["a", "d", "b", "c"]


def test_a_missing_or_malformed_fixture_is_refused(tmp_path):
    with pytest.raises(GithubFixtureError):
        load_github_fixture("nope", tmp_path)
    (tmp_path / "bad.json").write_text(json.dumps({"commits": [{"sha": "x"}]}))
    with pytest.raises(GithubFixtureError):
        load_github_fixture("bad", tmp_path)


def test_a_scenario_naming_a_missing_fixture_is_refused(tmp_path):
    shipped = (FIXTURES_DIR.parent.parent / "scenarios" / "bad-deploy.yaml").read_text()
    path = tmp_path / "bad-deploy.yaml"
    path.write_text(shipped.replace("github: bad-deploy", "github: no-such-fixture"))
    with pytest.raises(ScenarioError, match="no-such-fixture"):
        load_scenario(path)


async def test_bad_deploy_serves_the_deploy_and_the_commit_touching_api_items():
    (scenario,) = load_scenarios(only="bad-deploy")
    assert scenario.github == "bad-deploy"
    github = fixture_client(load_github_fixture(scenario.github), lambda: START)

    commits = await github.commits(REPO, START - timedelta(hours=48))
    text = describe_commits(REPO, 48, None, commits)
    diff = describe_diff(await github.commit(REPO, commits[1].sha), 8000)

    assert "Deploy shop 2" in text and "whole cents" in text
    assert "/api/items" in diff and "price_cents" in diff
    assert "demo/shop/migrations/0002_price_cents.sql" in diff


async def test_the_commit_cases_plant_their_payload_as_the_newest_commit():
    cases = [c for c in load_cases() if c.channel == "commit"]
    assert {c.id for c in cases} == {"commit-fake-approval", "commit-revert-order"}
    for case in cases:
        fixture = case.github()
        assert fixture is not None
        github = fixture_client(fixture, lambda: START)
        newest = (await github.commits(REPO, START - timedelta(hours=48)))[0]
        assert newest.message == case.payload
        assert case.marker in describe_commits(REPO, 48, None, [newest])
    assert all(c.github() is None for c in load_cases() if c.channel != "commit")
