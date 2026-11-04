"""Time the snapshot's first ingest, a second one with nothing new, and one search, inside the
backend image with the baked-in model.

    docker run --rm --network none -v "$PWD/docs/spikes/knowledge-chunks:/bench:ro" \
        --entrypoint python bleep-backend /bench/ingest_in_image.py
"""

import asyncio
import tempfile
import time
from pathlib import Path

from bleep.knowledge.index import KnowledgeIndex
from bleep.knowledge.ingest import load_snapshot
from bleep.memory.embeddings import FastEmbedEmbedder
from bleep.store.db import Database


async def main() -> None:
    with tempfile.TemporaryDirectory() as folder:
        db = Database(Path(folder) / "bleep.db")
        await db.migrate()
        index = KnowledgeIndex(db, FastEmbedEmbedder())
        for label in ("first sync", "second sync"):
            start = time.perf_counter()
            result = await index.sync("docker", load_snapshot())
            print(f"{label}: {result}, {time.perf_counter() - start:.2f} s")
        start = time.perf_counter()
        hits = await index.search("what does exit code 137 mean")
        print(f"search: {(time.perf_counter() - start) * 1000:.0f} ms, first hit {hits[0].title!r}")


asyncio.run(main())
