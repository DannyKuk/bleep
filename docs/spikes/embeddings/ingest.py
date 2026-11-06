"""Time the Docker docs snapshot's first ingest (27 pages, 444 chunks) and one search with
bge-small or bge-m3, inside the backend image, with the process's peak memory (#146).

    docker run --rm --network none -e MODEL=m3 -v "$PWD/docs/spikes/embeddings:/bench:ro" \
        --entrypoint python bleep-spike-146:m3 /bench/ingest.py
"""

import asyncio
import resource
import tempfile
import time
from pathlib import Path

from models import embedder

from bleep.knowledge.index import KnowledgeIndex
from bleep.knowledge.ingest import load_snapshot
from bleep.store.db import Database


def peak_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # KiB on Linux


async def main() -> None:
    model = embedder()
    with tempfile.TemporaryDirectory() as folder:
        db = Database(Path(folder) / "bleep.db")
        await db.migrate()
        index = KnowledgeIndex(db, model)
        start, cpu = time.perf_counter(), time.process_time()
        result = await index.sync("docker", load_snapshot())
        wall, cpu = time.perf_counter() - start, time.process_time() - cpu
        print(f"{model.model}, threads {model.threads}")
        print(f"first sync: {result}, {wall:.1f} s, {cpu:.1f} s of CPU, model load included")
        start = time.perf_counter()
        hits = await index.search("what does exit code 137 mean")
        print(f"search: {(time.perf_counter() - start) * 1000:.0f} ms, first {hits[0].title!r}")
        print(f"peak memory: {peak_mb():.0f} MB")


asyncio.run(main())
