"""Measure one embedder on German service docs, #146's knowledge questions and #306's recall
queries (#377), with what a full first ingest would embed, so cost can be worked out.

    cd backend && uv run python ../docs/spikes/embeddings-german/measure.py local
    cd backend && EMBEDDINGS_API_KEY=... uv run python ../docs/spikes/embeddings-german/measure.py \
        openai --base-url https://openrouter.ai/api/v1 --model google/gemini-embedding-2

`local` is Bleep's default as Bleep runs it (fastembed, bge-small, two threads); `openai` is
Bleep's `OpenAICompatEmbedder`, with the key read from the environment variable named by
`--key-env` (default EMBEDDINGS_API_KEY) and never printed or written. Every text goes through
`CountingEmbedder`, so the totals are what was embedded. `--list-chunks` prints the German set's
chunk ids and exits (no embedder). `--out` writes the numbers as JSON.

(a) docs/spikes/embeddings-german/ (this set, chunked and indexed as Bleep ingests a repo's docs),
(b) backend/evals/knowledge/questions*.yaml over the Docker docs snapshot, (c) recall.py's 10
stored records and 18 queries with the cut-off calibration. Nothing here calls a chat model.
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import yaml
from pydantic import SecretStr

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "backend"))  # for evals, which is not part of the bleep package
sys.path.insert(0, str(HERE.parent / "embeddings"))  # for recall.py's records and queries

import recall  # noqa: E402

from bleep.knowledge.chunking import MAX_TOKENS, OVERLAP, chunk_markdown  # noqa: E402
from bleep.knowledge.index import EMBED_BATCH, KnowledgeIndex, embedded_text  # noqa: E402
from bleep.knowledge.ingest import SNAPSHOT_DIR, load_snapshot, repo_documents  # noqa: E402
from bleep.memory.embeddings import (  # noqa: E402
    Embedder,
    FastEmbedEmbedder,
    OpenAICompatEmbedder,
    min_score_for,
)
from bleep.schemas.github import RepoFile  # noqa: E402
from bleep.store.db import Database  # noqa: E402
from evals.knowledge import MULTILINGUAL_QUESTIONS, QUESTIONS, ask, load_questions, summary  # noqa: E402

DOCS = HERE / "docs"
SET_QUERIES = HERE / "queries.yaml"
REPO = "kramer/stack"
URL = "https://example.invalid/{}"
TOKEN_ESTIMATE_CHARS = 4  # a rough chars-per-token for the cost estimate; real counts differ
LATENCY_SAMPLES = 10


class CountingEmbedder:
    """Wraps an embedder and counts what goes through it."""

    def __init__(self, inner: Embedder) -> None:
        self.inner = inner
        self.calls = 0
        self.texts = 0
        self.chars = 0

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def min_score(self) -> float:
        return self.inner.min_score

    def embed(self, texts: list[str]) -> np.ndarray:
        self.calls += 1
        self.texts += len(texts)
        self.chars += sum(len(t) for t in texts)
        return self.inner.embed(texts)

    def snapshot(self) -> tuple[int, int, int]:
        return self.calls, self.texts, self.chars


def build_embedder(args: argparse.Namespace) -> Embedder:
    if args.provider == "local":
        cache = os.environ.get("FASTEMBED_CACHE_PATH")
        return FastEmbedEmbedder(args.model or "BAAI/bge-small-en-v1.5", cache_dir=cache)
    key = os.environ.get(args.key_env)
    if not args.base_url or not args.model:
        sys.exit("openai needs --base-url and --model")
    if not key:
        sys.exit(f"openai needs the key in the environment variable {args.key_env}")
    return OpenAICompatEmbedder(
        args.base_url, args.model, api_key=SecretStr(key), min_score=args.min_score
    )


def set_documents() -> list:
    files = [
        RepoFile(path=p.name, sha="-", text=p.read_text(encoding="utf-8"), url=URL.format(p.name))
        for p in sorted(DOCS.glob("*.md"))
    ]
    return repo_documents(REPO, files)


def chunk_table() -> dict[str, dict]:
    """Every chunk of the set by id ("<file>#<position>"), cut as Bleep's ingest cuts a repo's
    docs, with the exact text that is embedded."""
    table = {}
    for doc in set_documents():
        name = doc.id.rsplit(":", 1)[-1]
        for position, chunk in enumerate(chunk_markdown(doc.text, MAX_TOKENS, OVERLAP)):
            heading = " > ".join(chunk.heading_path)
            table[f"{name}#{position}"] = {
                "url": doc.url,
                "heading": heading,
                "text": chunk.text,
                "embedded": embedded_text(doc.title, heading, chunk.text),
            }
    return table


def list_chunks() -> None:
    for chunk_id, c in chunk_table().items():
        print(f"{chunk_id:28} {c['heading']}")


async def fresh_index(embedder: Embedder) -> tuple[KnowledgeIndex, tempfile.TemporaryDirectory]:
    folder = tempfile.TemporaryDirectory(prefix="bleep-embeddings-german-")
    db = Database(Path(folder.name) / "bleep.db")
    await db.migrate()
    return KnowledgeIndex(db, embedder), folder


def ranks(found: list[int | None]) -> dict[str, int]:
    return {
        "n": len(found),
        "first": sum(r == 1 for r in found),
        "top5": sum(r is not None and r <= 5 for r in found),
    }


async def measure_set(embedder: CountingEmbedder) -> dict:
    """(a) The German set: the right chunk first / among the five chunks `knowledge_search`
    returns (the best chunk of each of the five closest documents)."""
    table = chunk_table()
    queries = yaml.safe_load(SET_QUERIES.read_text(encoding="utf-8"))
    unknown = [q["chunk"] for q in queries if q["chunk"] not in table]
    if unknown:
        raise SystemExit(f"queries name unknown chunks: {unknown}")
    by_text = {c["text"]: chunk_id for chunk_id, c in table.items()}
    index, folder = await fresh_index(embedder)
    before = embedder.snapshot()
    start = time.perf_counter()
    await index.sync("repo", set_documents(), prefix=f"repo:{REPO}:")
    ingest_s = time.perf_counter() - start
    embedded = embedder.snapshot()[1] - before[1]
    rows = []
    for q in queries:
        hits = await index.search(q["query"], k=5, sources={"repo"})
        found = [by_text.get(h.text, "?") for h in hits]
        rank = found.index(q["chunk"]) + 1 if q["chunk"] in found else None
        rows.append({**q, "rank": rank, "found": found})
    folder.cleanup()
    by_lang = {lang: ranks([r["rank"] for r in rows if r["lang"] == lang]) for lang in ("en", "de")}
    return {
        "chunks": len(table),
        "texts_embedded": embedded,
        "ingest_s": round(ingest_s, 1),
        "all": ranks([r["rank"] for r in rows]),
        **by_lang,
        "rows": rows,
    }


async def measure_knowledge(embedder: CountingEmbedder) -> dict:
    """(b) #146's questions over the Docker docs snapshot, ingested once for both sets."""
    index, folder = await fresh_index(embedder)
    docs = load_snapshot(SNAPSHOT_DIR)
    urls = {d.url: d.id.removeprefix("docker:") for d in docs}
    before = embedder.snapshot()
    start = time.perf_counter()
    await index.sync("docker", docs)
    ingest_s = time.perf_counter() - start
    calls, texts, chars = (a - b for a, b in zip(embedder.snapshot(), before, strict=True))
    out = {
        "snapshot": {
            "pages": len(docs),
            "texts": texts,
            "chars": chars,
            "ingest_s": round(ingest_s, 1),
        }
    }
    for label, path in (("english", QUESTIONS), ("german", MULTILINGUAL_QUESTIONS)):
        answers = [await ask(index, q, urls) for q in load_questions(path)]
        out[label] = ranks([a.rank for a in answers]) | {
            "search_ms_median": summary(answers)["search_ms_median"],
            "misses": [a.question for a in answers if a.rank is None],
        }
    folder.cleanup()
    return out


def measure_recall(embedder: CountingEmbedder) -> dict:
    """(c) #306's records and queries: ranks, then the cut-off calibration."""
    ids = list(recall.STORED)
    records = embedder.embed(list(recall.STORED.values()))
    scores = embedder.embed([q for _, q, _ in recall.QUERIES]) @ records.T
    found, right, wrong = [], [], []
    for (_, _, expected), row in zip(recall.QUERIES, scores, strict=True):
        want = ids.index(expected)
        found.append(int(np.argsort(-row).tolist().index(want)) + 1)
        right.append(float(row[want]))
        wrong += [float(row[i]) for i in range(len(ids)) if i != want]
    cutoff = min_score_for(embedder.inner.model)

    def returned(threshold: float) -> int:
        return sum(
            recall.bleep_position(g, q, row, ids, ids.index(e), threshold).isdigit()
            for (g, q, e), row in zip(recall.QUERIES, scores, strict=True)
        )

    sweep = [
        {
            "min_score": t,
            "right_returned": returned(t),
            "others_passing": sum(s >= t for s in wrong),
        }
        for t in (round(0.30 + 0.025 * i, 3) for i in range(27))
    ]
    most = sweep[0]["right_returned"]  # what the top-k cut allows even with no threshold
    all_returned = [s["min_score"] for s in sweep if s["right_returned"] == most]
    return {
        **ranks(found),
        "lowest_right": round(min(right), 3),
        "mean_right": round(float(np.mean(right)), 3),
        "highest_other": round(max(wrong), 3),
        "mean_other": round(float(np.mean(wrong)), 3),
        "others_total": len(wrong),
        "bleep_cutoff": cutoff,
        "returned_at_cutoff": returned(cutoff),
        "others_over_cutoff": sum(s >= cutoff for s in wrong),
        "highest_threshold_keeping_every_returnable": max(all_returned) if all_returned else None,
        "sweep": sweep,
        "texts": len(recall.STORED) + len(recall.QUERIES),
    }


def measure_latency(embedder: CountingEmbedder, texts: list[str]) -> dict:
    """Per call: one text at a time, and a batch of Bleep's EMBED_BATCH. The first call, which
    loads a local model or opens a connection, is counted apart."""
    start = time.perf_counter()
    first = embedder.embed(texts[:1])
    first_ms = (time.perf_counter() - start) * 1000
    singles = []
    for text in texts[1 : LATENCY_SAMPLES + 1]:
        start = time.perf_counter()
        embedder.embed([text])
        singles.append((time.perf_counter() - start) * 1000)
    batch = texts[:EMBED_BATCH]
    start = time.perf_counter()
    vectors = embedder.embed(batch)
    batch_ms = (time.perf_counter() - start) * 1000
    return {
        "vector_size": int(first.shape[1]),
        "first_call_ms": round(first_ms, 1),
        "one_text_ms_median": round(statistics.median(singles), 1),
        "one_text_ms_max": round(max(singles), 1),
        f"batch_of_{len(batch)}_ms": round(batch_ms, 1),
        "batch_ms_per_text": round(batch_ms / len(batch), 1),
        "unit_length": bool(np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-3)),
    }


def row(label: str, counts: dict) -> str:
    return f"| {label} | {counts['n']} | {counts['first']} | {counts['top5']} |"


def render(result: dict) -> str:
    s, k, r, lat = result["set"], result["knowledge"], result["recall"], result["latency"]
    cut = r["bleep_cutoff"]
    lines = [
        f"embedder: {result['embedder']}   min_score in Bleep: {cut}",
        "",
        "| Set | Questions | First | Top 5 |",
        "|---|---|---|---|",
        row("German set, all", s["all"]),
        row("German set, English queries", s["en"]),
        row("German set, German queries", s["de"]),
        row("#146 English questions", k["english"]),
        row("#146 German questions", k["german"]),
        row("#306 recall queries (records ranked)", r),
        "",
        f"recall: lowest right {r['lowest_right']}, highest other {r['highest_other']}; at Bleep's"
        f" cut-off {cut}: {r['returned_at_cutoff']} of {r['n']} right returned,"
        f" {r['others_over_cutoff']} of {r['others_total']} others pass; highest threshold in"
        f" 0.025 steps keeping every right record the top-k cut allows:"
        f" {r['highest_threshold_keeping_every_returnable']}",
        "",
        "| min_score | Right returned | Others passing |",
        "|---|---|---|",
        *(
            f"| {t['min_score']:.3f} | {t['right_returned']} | {t['others_passing']} |"
            for t in r["sweep"]
        ),
        "",
        f"latency: {json.dumps(lat)}",
        "",
        "full first ingest, texts embedded: " + json.dumps(result["cost"]),
    ]
    return "\n".join(lines)


async def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("provider", nargs="?", choices=["local", "openai"])
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument("--key-env", default="EMBEDDINGS_API_KEY")
    parser.add_argument("--min-score", type=float, default=None)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--list-chunks", action="store_true")
    args = parser.parse_args(argv)
    if args.list_chunks:
        list_chunks()
        return
    if not args.provider:
        parser.error("provider is required")
    embedder = CountingEmbedder(build_embedder(args))
    sample = [c["embedded"] for c in chunk_table().values()]
    latency = measure_latency(embedder, sample)
    set_result = await measure_set(embedder)
    knowledge = await measure_knowledge(embedder)
    recall_result = measure_recall(embedder)
    snapshot = knowledge["snapshot"]
    set_chars = sum(len(c["embedded"]) for c in chunk_table().values())
    cost = {
        "docker_snapshot": {"texts": snapshot["texts"], "chars": snapshot["chars"]},
        "repo_docs_this_set": {"texts": set_result["texts_embedded"], "chars": set_chars},
        "memory_per_incident": {"texts": 1, "note": "plus one query per recall"},
        "first_ingest_texts": snapshot["texts"] + set_result["texts_embedded"],
        "first_ingest_chars": snapshot["chars"] + set_chars,
        "estimated_tokens": (snapshot["chars"] + set_chars) // TOKEN_ESTIMATE_CHARS,
        "whole_run_texts": embedder.texts,
        "whole_run_chars": embedder.chars,
        "whole_run_estimated_tokens": embedder.chars // TOKEN_ESTIMATE_CHARS,
        "whole_run_calls": embedder.calls,
        "chars_per_token_assumed": TOKEN_ESTIMATE_CHARS,
    }
    result = {
        "embedder": embedder.name,
        "set": set_result,
        "knowledge": knowledge,
        "recall": recall_result,
        "latency": latency,
        "cost": cost,
    }
    print(render(result))
    if args.out:
        args.out.write_text(
            json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        print(f"\nraw: {args.out}", file=sys.stderr)


asyncio.run(main(sys.argv[1:]))
