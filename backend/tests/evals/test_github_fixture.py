import json
import re
from datetime import UTC, datetime, timedelta

import pytest

from bleep.hosts.github import NotFound
from bleep.schemas.services import Service
from bleep.tools.github import (
    COMMITS,
    describe_commits,
    describe_deploy,
    describe_diff,
    part_size,
)
from evals import long_deploy
from evals.github import (
    FIXTURES_DIR,
    FixtureCommit,
    FixtureDeploy,
    FixtureDeploys,
    FixtureFile,
    GithubFixture,
    GithubFixtureError,
    fixture_client,
    load_github_fixture,
    named_commits,
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


def test_named_commits_are_the_fixtures_commits_a_text_names_by_a_sha_start():
    texts = [
        f"commit {'B' * 7}: 'commit b' did it; see {'a' * 12}.",
        f"not {'c' * 6}, nor shop:{'c' * 7}, nor {'d' * 7}",
    ]

    assert named_commits(FIXTURE, texts) == ["a" * 7, "b" * 7]
    assert named_commits(FIXTURE, ["nothing here"]) == []


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


SHOP = Service(id="demo/compose:shop", host_id="demo", name="shop", source="compose", containers=[])


async def test_the_compare_lists_what_head_has_that_base_lacks_oldest_first():
    github = fixture_client(FIXTURE, lambda: START)

    ahead = await github.compare(REPO, "c" * 40, "a" * 7)
    behind = await github.compare(REPO, "a" * 40, "c" * 40)

    assert ahead.status == "ahead" and ahead.total_commits == 2
    assert [c.sha[0] for c in ahead.commits] == ["a", "b"]  # newest first once read
    assert behind.status == "behind" and behind.behind_by == 2 and behind.commits == []
    with pytest.raises(NotFound):
        await github.compare(REPO, "1234567", "a" * 7)


async def test_the_list_from_a_sha_starts_there_and_an_unknown_one_is_not_found():
    github = fixture_client(FIXTURE, lambda: START)

    listed = await github.commits(REPO, None, sha="b" * 40)

    assert [c.sha[0] for c in listed] == ["b", "c"]
    with pytest.raises(NotFound):
        await github.commits(REPO, None, sha="1234567")


def test_a_deploy_must_name_the_fixtures_own_commits():
    deploy = FixtureDeploy(commit="a" * 40, previous="d" * 40, minutes_ago=5)
    with pytest.raises(ValueError, match="does not have"):
        GithubFixture(commits=FIXTURE.commits, deploy=deploy)


async def test_without_a_deploy_the_fixture_names_no_running_commit():
    assert await FixtureDeploys(FIXTURE, START).running_commit(SHOP) is None


async def test_bad_deploy_old_hides_the_cause_from_the_window_and_shows_it_in_the_deploy():
    (scenario,) = load_scenarios(only="bad-deploy-old")
    assert scenario.github == "bad-deploy-old" and scenario.fault == "bad-deploy"
    fixture = load_github_fixture(scenario.github)
    github = fixture_client(fixture, lambda: START)

    for hours in (48, 720):  # the default window and the widest: the newest 20 either way
        window = await github.commits(REPO, START - timedelta(hours=hours), limit=COMMITS)
        assert "whole cents" not in describe_commits(REPO, hours, None, window)
    running = await FixtureDeploys(fixture, START).running_commit(SHOP)
    assert running is not None and running.previous is not None
    assert running.since == START - timedelta(hours=72)
    deployed = describe_deploy(
        REPO, running, await github.compare(REPO, running.previous, running.sha)
    )

    assert "this deploy brought 2 commits" in deployed
    assert "Deploy shop 2" in deployed and "whole cents" in deployed
    assert "Reword the setup guide" not in deployed  # pushed after the deploy
    assert "Log the shop version" not in deployed  # deployed before


async def test_the_compare_pages_from_the_oldest_and_lists_the_newest_250_unpaged():
    commits = [
        FixtureCommit(sha=f"{i:040x}", minutes_ago=1000 - i, author="dev", message=f"c{i}")
        for i in range(999, -1, -1)  # newest first
    ]
    github = fixture_client(GithubFixture(commits=commits), lambda: START)
    base, head = f"{0:040x}", f"{999:040x}"

    unpaged = await github.compare(REPO, base, head)
    page = await github.compare_older(REPO, base, head, 300, 10)
    past = await github.compare_older(REPO, base, head, 999)

    assert unpaged.total_commits == 999 and len(unpaged.commits) == 250
    assert unpaged.commits[0].message == "c999" and unpaged.commits[-1].message == "c750"
    assert [c.message for c in page.commits] == [f"c{i}" for i in range(699, 689, -1)]
    assert past.commits == [] and past.total_commits == 999


def test_the_long_deploy_fixture_is_what_the_committed_script_builds():
    committed = (FIXTURES_DIR / "bad-deploy-long.json").read_text(encoding="utf-8")

    assert committed == long_deploy.render()
    fixture = load_github_fixture("bad-deploy-long")
    assert len({c.sha for c in fixture.commits}) == len(fixture.commits)


async def test_bad_deploy_long_hides_the_cause_from_the_window_and_the_first_250():
    (scenario,) = load_scenarios(only="bad-deploy-long")
    assert scenario.github == "bad-deploy-long" and scenario.fault == "bad-deploy"
    fixture = load_github_fixture(scenario.github)
    github = fixture_client(fixture, lambda: START)

    window = await github.commits(REPO, START - timedelta(hours=720), limit=100)
    narrowed = await github.commits(
        REPO, START - timedelta(hours=720), path="demo/shop", limit=COMMITS
    )
    assert "whole cents" not in describe_commits(REPO, 720, None, window)
    assert narrowed == []  # the cause is older than the widest window, with or without a path
    running = await FixtureDeploys(fixture, START).running_commit(SHOP)
    assert running is not None and running.previous is not None
    assert running.since == START - timedelta(days=41)

    unpaged = await github.compare(REPO, running.previous, running.sha)
    first = describe_deploy(REPO, running, unpaged)
    assert "this deploy brought 300 commits" in first and "Deploy shop 2" in first
    assert "whole cents" not in first
    assert len(unpaged.commits) == 250 and unpaged.total_commits == 300


async def test_following_the_notes_skip_reaches_the_cause_in_the_last_part():
    fixture = load_github_fixture("bad-deploy-long")
    github = fixture_client(fixture, lambda: START)
    running = await FixtureDeploys(fixture, START).running_commit(SHOP)
    assert running is not None and running.previous is not None
    unpaged = await github.compare(REPO, running.previous, running.sha)
    parts = [describe_deploy(REPO, running, unpaged)]

    skips = [0]
    while match := re.search(r"with skip=(\d+) for them\)", parts[-1]):
        skip = int(match[1])
        count = part_size(skip - skips[-1])  # as the tool asks for it
        older = await github.compare_older(REPO, running.previous, running.sha, skip, count)
        parts.append(describe_deploy(REPO, running, older, skip=skip))
        skips.append(skip)

    shas = [sha for part in parts for sha in re.findall(r"^- ([0-9a-f]{7}) ", part, re.M)]
    assert len(shas) == 300 and len(set(shas)) == 300  # the whole deploy, no gap, no repeat
    assert skips == [0, 112, 206]  # three deployed_commits calls read the whole deploy
    assert "whole cents" in parts[-1] and "whole cents" not in "".join(parts[:-1])
    assert shas[0] == "d9f9be1" and shas[-1] == "3350063"


async def test_bad_deploy_deep_puts_the_only_explaining_commits_past_the_first_part():
    (scenario,) = load_scenarios(only="bad-deploy-deep")
    assert scenario.github == "bad-deploy-deep" and scenario.fault == "bad-deploy"
    committed = (FIXTURES_DIR / "bad-deploy-deep.json").read_text(encoding="utf-8")
    assert committed == long_deploy.render("bad-deploy-deep")
    fixture = load_github_fixture("bad-deploy-deep")
    github = fixture_client(fixture, lambda: START)
    running = await FixtureDeploys(fixture, START).running_commit(SHOP)
    assert running is not None and running.previous is not None
    unpaged = await github.compare(REPO, running.previous, running.sha)
    parts = [describe_deploy(REPO, running, unpaged)]

    skips = [0]
    while match := re.search(r"with skip=(\d+) for them\)", parts[-1]):
        skip = int(match[1])
        older = await github.compare_older(
            REPO, running.previous, running.sha, skip, part_size(skip - skips[-1])
        )
        parts.append(describe_deploy(REPO, running, older, skip=skip))
        skips.append(skip)

    explaining = ("Deploy shop 2", "whole cents", "SHOP_VERSION", "price")
    assert all(word not in "".join(parts[:-1]) for word in explaining)
    assert "Deploy shop 2" in parts[-1] and "whole cents" in parts[-1]
    assert skips == [0, 112, 206]
    shas = [sha for part in parts for sha in re.findall(r"^- ([0-9a-f]{7}) ", part, re.M)]
    assert len(shas) == 300 and len(set(shas)) == 300
    assert shas[-1] == "3350063" and shas[-2] == "d9f9be1"
    assert len(unpaged.commits) == 250 and unpaged.total_commits == 300
    assert "d9f9be1" not in parts[0]
