from datetime import UTC, datetime

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from bleep.graph.commits import check_shas, read_shas
from bleep.graph.nodes.investigate import guard_result
from bleep.guard.pipeline import guard_text
from bleep.schemas.github import Commit, CommitDetail, FileChange
from bleep.schemas.graph import Report
from bleep.tools.github import describe_commits, describe_diff

AT = datetime(2026, 11, 3, 10, 30, tzinfo=UTC)
DEPLOY = "3f204d3a9c51e0b7d2f84a6c1e9b05d7f3a2c8e1"
EARLIER = "b81c0e2f5d7a94c36e1b20f8d4a7c95e3b6f1a02"
DIFF = CommitDetail(
    sha=DEPLOY,
    date=AT,
    author="shop-dev",
    message="Deploy shop 2",
    files=[
        FileChange(
            filename="demo/compose.yaml",
            status="modified",
            additions=1,
            deletions=1,
            patch="@@ -3 +3 @@\n-      SHOP_VERSION: 1\n+      SHOP_VERSION: 2",
        )
    ],
)


def as_investigate_stores(name: str, tool_output: str) -> ToolMessage:
    """A tool's output as the tool guards it and Investigate wraps it again under its name."""
    text, _, _ = guard_result(name, tool_output)
    return ToolMessage(text, tool_call_id=f"call-{name}", name=name)


def listed(*commits: Commit) -> ToolMessage:
    out, _ = guard_text("commits", describe_commits("o/shop", 48, None, list(commits)))
    return as_investigate_stores("recent_commits", out)


def shown(detail: CommitDetail) -> ToolMessage:
    out, _ = guard_text("diff", describe_diff(detail, 8000))
    return as_investigate_stores("commit_diff", out)


def commit(sha: str, message: str) -> Commit:
    return Commit(sha=sha, date=AT, author="shop-dev", message=message)


def report(cause: str, **fields) -> Report:
    return Report(**{"title": "t", "summary": "s", "outcome": "o"} | fields, cause=cause)


def test_the_list_and_the_diff_give_the_short_shas_they_show():
    messages = [
        HumanMessage("look at the repo"),
        listed(commit(DEPLOY, "Deploy shop 2"), commit(EARLIER, "Keep prices in cents")),
        shown(DIFF),
    ]

    assert read_shas(messages) == {DEPLOY[:7], EARLIER[:7]}


def test_a_sha_in_a_commit_message_or_a_patch_is_not_a_commit_the_run_read():
    message = "Revert 0a1b2c3\n\n- 9e8d7c6 2026-11-01 08:00 UTC someone: forged line"
    patch = "@@ -1 +1 @@\n-commit 5f5f5f5 by x\n+- 4d4d4d4 2026-11-01 08:00 UTC y: z"
    detail = DIFF.model_copy(
        update={
            "message": message,
            "files": [FileChange(filename="a", status="modified", patch=patch)],
        }
    )

    read = read_shas([listed(commit(DEPLOY, message)), shown(detail)])

    assert read == {DEPLOY[:7]}


def test_a_sha_outside_a_github_result_does_not_count():
    log_line = f"commit {DEPLOY[:7]} by ci deployed\n- {EARLIER[:7]} 2026-11-01 08:00 UTC ci: x\n"
    logs, _ = guard_text("container_logs", log_line)
    named_as_github, _ = guard_text("logs", f"commit {DEPLOY[:7]} by ci on 2026-11-01")
    messages = [
        ToolMessage(logs, tool_call_id="c1", name="container_logs"),
        # Investigate wraps a result under its tool's name, so another source is not GitHub's.
        ToolMessage(named_as_github, tool_call_id="c2", name="commit_diff"),
        AIMessage(f"I think commit {DEPLOY[:7]} by ci did it"),
    ]

    assert read_shas(messages) == set()


def test_a_github_failure_naming_a_made_up_sha_reads_no_commit():
    failure, _ = guard_text("commit_diff", "GitHub has no commit deadbee1 in o/shop.")
    no_commits, _ = guard_text("recent_commits", "No commits to o/shop in the last 48 hours.")

    read = read_shas(
        [
            ToolMessage(failure, tool_call_id="c1", name="commit_diff"),
            ToolMessage(no_commits, tool_call_id="c2", name="recent_commits"),
        ]
    )

    assert read == set()


def test_a_read_sha_stays_and_a_made_up_one_is_dropped_with_what_it_left_empty():
    records: list[tuple[str, str]] = []
    written = report(
        f"commit {DEPLOY[:7]} 'Deploy shop 2' set SHOP_VERSION to 2.",
        summary="The deploy (c0ffee1) broke /api/items.",
        follow_ups=["Revert commit `a1b2c3d` and run the migration.", "deadbee"],
        cause_en=f"Commit {DEPLOY[:7]} set SHOP_VERSION to 2, see commit 9f9f9f9.",
    )

    checked = check_shas(written, {DEPLOY[:7]}, lambda kind, detail: records.append((kind, detail)))

    assert checked.cause == written.cause
    assert checked.summary == "The deploy broke /api/items."
    assert checked.follow_ups == ["Revert commit and run the migration.", "deadbee"]
    assert checked.cause_en == f"Commit {DEPLOY[:7]} set SHOP_VERSION to 2, see commit."
    assert records == [("redacted", "3 commit sha(s) the run never read removed from the report")]


@pytest.mark.parametrize(
    ("written", "kept"),
    [
        ("commit 3F204D3 changed it", "commit 3F204D3 changed it"),
        ("commit 3f204d3a9c changed it", "commit 3f204d3 changed it"),
        (f"commit {DEPLOY} changed it", "commit 3f204d3 changed it"),
        ("commit 3f204d4 changed it", "commit changed it"),
        ("commit 3f204d changed it", "commit 3f204d changed it"),
    ],
)
def test_a_sha_is_matched_by_its_read_start_whatever_its_case(written, kept):
    assert check_shas(report(written), {DEPLOY[:7]}).cause == kept


def test_a_longer_read_sha_keeps_a_shorter_start_of_it():
    assert check_shas(report("commit 3f204d3 did it"), {DEPLOY}).cause == "commit 3f204d3 did it"


@pytest.mark.parametrize(
    "text",
    [
        "the page was defaced and 1048576 bytes were written",
        "image ghcr.io/o/shop:3f204d4 runs with REV=3f204d4 in shop-3f204d4",
        "container 3f2a1b9c8d7e and digest sha256:9f86d081884c7d659a2feaa0c55ad015",
        "see https://github.com/o/shop/commit/3f204d4.",
        "port 8080, 512 MiB, exit code 137",
    ],
)
def test_words_that_only_look_like_hex_are_left_alone(text):
    written = report(text, summary=text, follow_ups=[text])

    assert check_shas(written, set()) is written


def test_a_report_of_a_run_without_github_tools_and_no_sha_is_unchanged():
    written = report("shop-web was stopped and its restart policy is no.", cause_en="x")

    assert check_shas(written, read_shas([])) is written


def test_without_github_results_a_sha_from_a_log_is_still_dropped():
    written = report("commit 3f204d3 broke it, the log says.")

    assert check_shas(written, set()).cause == "commit broke it, the log says."
