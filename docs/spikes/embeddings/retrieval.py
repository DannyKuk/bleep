"""The knowledge retrieval check (backend/evals/knowledge.py) on bge-small or bge-m3 (#146): the
20 English questions and the 11 German and mixed-language ones, over the same English snapshot
with Bleep's chunk settings. One JSON line per question after a totals line goes to
docs/evals/data/embeddings-multilingual-<model>-<set>.jsonl.

    cd backend && MODEL=small|m3 uv run python ../docs/spikes/embeddings/retrieval.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))  # for evals, which is not part of the bleep package

from models import embedder  # noqa: E402

from bleep.knowledge.chunking import MAX_TOKENS, OVERLAP  # noqa: E402
from evals.knowledge import MULTILINGUAL_QUESTIONS, QUESTIONS, render, run  # noqa: E402

DATA_DIR = ROOT / "docs" / "evals" / "data"


async def main() -> None:
    key = os.environ.get("MODEL", "small")
    model = embedder(key)  # one embedder, so the second set does not load the model again
    for label, questions in (("english", QUESTIONS), ("multilingual", MULTILINGUAL_QUESTIONS)):
        answers, totals = await run(model, MAX_TOKENS, OVERLAP, questions_file=questions)
        raw = DATA_DIR / f"embeddings-multilingual-{key}-{label}.jsonl"
        with raw.open("w", encoding="utf-8") as out:
            out.write(json.dumps({"model": model.model, "totals": totals}) + "\n")
            for a in answers:
                out.write(a.model_dump_json() + "\n")
        print(f"## {model.model}, {label}\n\n{render(answers, totals)}\n\nraw: {raw}\n")


asyncio.run(main())
