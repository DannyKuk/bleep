"""The retrieval check for knowledge search: hand-written questions, each with the snapshot page
that answers it, searched with the real embedding model. Reports hit@1, hit@3, hit@5 and the mean
reciprocal rank (by page, over the top 5), with what ingest and search cost.

    cd backend && uv run python -m evals.knowledge [--max-tokens 300] [--overlap 50] [--label x]
        [--questions evals/knowledge/questions_multilingual.yaml]

The snapshot is ingested into a temporary database with Bleep's local embedder (it is downloaded
on a first run outside the image). One JSON line per question goes to
`docs/evals/data/knowledge-<time>[-label].jsonl`, and the tables are printed for the dated report.
Nothing here calls a chat model. See docs/design/knowledge.md.
"""

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from bleep.knowledge.chunking import MAX_TOKENS, OVERLAP
from bleep.knowledge.index import KnowledgeIndex
from bleep.knowledge.ingest import SNAPSHOT_DIR, load_snapshot
from bleep.memory.embeddings import Embedder, FastEmbedEmbedder
from bleep.store.db import Database

QUESTIONS = Path(__file__).parent / "knowledge" / "questions.yaml"
# German and mixed-language questions over the same English snapshot (#146).
MULTILINGUAL_QUESTIONS = Path(__file__).parent / "knowledge" / "questions_multilingual.yaml"
DATA_DIR = Path(__file__).resolve().parents[2] / "docs" / "evals" / "data"
K = 5


class KnowledgeEvalError(ValueError):
    """The questions file is malformed or names a page the snapshot lacks."""


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str
    page: str  # the snapshot page's file name without .md


class Answer(BaseModel):
    question: str
    page: str
    rank: int | None  # 1-based position of the page among the top K pages; None when absent
    found: list[str]  # the top K pages, closest first
    score: float | None  # the expected page's score, when found
    ms: float


def load_questions(path: Path = QUESTIONS, snapshot: Path = SNAPSHOT_DIR) -> list[Question]:
    try:
        questions = TypeAdapter(list[Question]).validate_python(
            yaml.safe_load(path.read_text(encoding="utf-8"))
        )
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise KnowledgeEvalError(f"{path}: {error}") from error
    pages = {p.stem for p in snapshot.glob("*.md")}
    unknown = sorted({q.page for q in questions} - pages)
    if unknown:
        raise KnowledgeEvalError(f"{path}: no snapshot page {', '.join(unknown)}")
    return questions


def page_of(url: str, urls: dict[str, str]) -> str:
    return urls.get(url, url)


async def ask(index: KnowledgeIndex, question: Question, urls: dict[str, str]) -> Answer:
    start = time.perf_counter()
    hits = await index.search(question.question, k=K, sources={"docker"})
    ms = (time.perf_counter() - start) * 1000
    found = [page_of(h.url, urls) for h in hits]
    rank = found.index(question.page) + 1 if question.page in found else None
    return Answer(
        question=question.question,
        page=question.page,
        rank=rank,
        found=found,
        score=hits[rank - 1].score if rank else None,
        ms=round(ms, 1),
    )


def summary(answers: Sequence[Answer]) -> dict[str, float]:
    n = len(answers)

    def hit(at: int) -> float:
        return sum(a.rank is not None and a.rank <= at for a in answers) / n

    return {
        "questions": n,
        "hit@1": hit(1),
        "hit@3": hit(3),
        "hit@5": hit(5),
        "mrr@5": sum(1 / a.rank for a in answers if a.rank) / n,
        "search_ms_median": statistics.median(a.ms for a in answers),
    }


async def run(
    embedder: Embedder,
    max_tokens: int,
    overlap: int,
    snapshot: Path = SNAPSHOT_DIR,
    questions_file: Path = QUESTIONS,
) -> tuple[list[Answer], dict[str, float]]:
    questions = load_questions(questions_file, snapshot)
    docs = load_snapshot(snapshot)
    urls = {d.url: d.id.removeprefix("docker:") for d in docs}
    with tempfile.TemporaryDirectory(prefix="bleep-knowledge-") as folder:
        db = Database(Path(folder) / "bleep.db")
        await db.migrate()
        index = KnowledgeIndex(db, embedder, max_tokens=max_tokens, overlap=overlap)
        start = time.perf_counter()
        await index.sync("docker", docs)
        ingest_s = time.perf_counter() - start
        conn = await db.connect()
        try:
            async with conn.execute("SELECT COUNT(*) AS n FROM knowledge_chunks") as cursor:
                chunks = (await cursor.fetchone())["n"]
        finally:
            await conn.close()
        answers = [await ask(index, q, urls) for q in questions]
    totals = summary(answers) | {
        "pages": len(docs),
        "chunks": chunks,
        "ingest_s": round(ingest_s, 1),
        "max_tokens": max_tokens,
        "overlap": overlap,
    }
    return answers, totals


def render(answers: Sequence[Answer], totals: dict[str, float]) -> str:
    lines = [
        "| Chunk limit | Overlap | Pages | Chunks | Ingest | hit@1 | hit@3 | hit@5 | MRR@5 |"
        " Search (median) |",
        "|---|---|---|---|---|---|---|---|---|---|",
        f"| {totals['max_tokens']} | {totals['overlap']} | {totals['pages']} | {totals['chunks']}"
        f" | {totals['ingest_s']} s | {totals['hit@1']:.0%} | {totals['hit@3']:.0%}"
        f" | {totals['hit@5']:.0%} | {totals['mrr@5']:.2f} | {totals['search_ms_median']:.0f} ms |",
        "",
        "| # | Question | Expected page | Rank | Top 5 |",
        "|---|---|---|---|---|",
    ]
    for n, a in enumerate(answers, start=1):
        lines.append(
            f"| {n} | {a.question} | `{a.page}` | {a.rank or 'miss'} | {', '.join(a.found)} |"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.knowledge", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--max-tokens", type=int, default=MAX_TOKENS)
    parser.add_argument("--overlap", type=int, default=OVERLAP)
    parser.add_argument("--label", default="")
    parser.add_argument("--questions", type=Path, default=QUESTIONS)
    args = parser.parse_args(argv)
    answers, totals = asyncio.run(
        run(FastEmbedEmbedder(), args.max_tokens, args.overlap, questions_file=args.questions)
    )
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    raw = DATA_DIR / f"knowledge-{stamp}{'-' + args.label if args.label else ''}.jsonl"
    with raw.open("w", encoding="utf-8") as out:
        out.write(json.dumps({"totals": totals}) + "\n")
        for a in answers:
            out.write(a.model_dump_json() + "\n")
    print(render(answers, totals))
    print(f"\nraw: {raw}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
