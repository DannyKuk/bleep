import pytest

from bleep.knowledge.ingest import SNAPSHOT_DIR
from bleep.memory.embeddings import FakeEmbedder
from evals.knowledge import (
    QUESTIONS,
    Answer,
    KnowledgeEvalError,
    load_questions,
    render,
    run,
    summary,
)

PAGE = """---
title: {title}
source_url: https://docs.docker.com/{slug}/
license: Apache-2.0
---

# {title}

{body}
"""


def snapshot(tmp_path, pages: dict[str, str]):
    folder = tmp_path / "docs"
    folder.mkdir()
    for slug, body in pages.items():
        (folder / f"{slug}.md").write_text(PAGE.format(title=slug, slug=slug, body=body))
    return folder


def answer(rank: int | None) -> Answer:
    return Answer(question="q", page="p", rank=rank, found=[], score=None, ms=1.0)


def test_the_shipped_questions_are_20_and_name_real_pages():
    questions = load_questions(QUESTIONS, SNAPSHOT_DIR)

    assert len(questions) == 20
    assert len({q.question for q in questions}) == 20


def test_a_question_naming_a_missing_page_is_refused(tmp_path):
    path = tmp_path / "q.yaml"
    path.write_text("- question: where?\n  page: nowhere\n")

    with pytest.raises(KnowledgeEvalError, match="nowhere"):
        load_questions(path, SNAPSHOT_DIR)


def test_summary_counts_hits_by_rank_and_the_reciprocal_rank():
    totals = summary([answer(1), answer(2), answer(4), answer(None)])

    assert (totals["hit@1"], totals["hit@3"], totals["hit@5"]) == (0.25, 0.5, 0.75)
    assert totals["mrr@5"] == pytest.approx((1 + 1 / 2 + 1 / 4) / 4)


async def test_a_run_ranks_the_expected_page_among_the_top_five(tmp_path):
    folder = snapshot(
        tmp_path,
        {"volumes": "named volumes keep database data", "tmpfs": "tmpfs mounts live in memory"},
    )
    questions = tmp_path / "q.yaml"
    questions.write_text(
        "- question: how do named volumes keep database data\n  page: volumes\n"
        "- question: tmpfs memory mounts\n  page: tmpfs\n"
    )

    answers, totals = await run(FakeEmbedder(), 300, 50, snapshot=folder, questions_file=questions)

    assert [a.rank for a in answers] == [1, 1]
    assert (totals["pages"], totals["chunks"], totals["hit@5"]) == (2, 2, 1.0)
    assert "| 1 | how do named volumes keep database data | `volumes` | 1 |" in render(
        answers, totals
    )
