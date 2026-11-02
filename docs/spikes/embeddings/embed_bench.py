"""Time the local embedder inside the backend image: model load, one text, a batch of 32, the CPU
one embed costs, and the process's peak memory. THREADS=n overrides Bleep's thread count.

    docker run --rm --network none -v "$PWD/docs/spikes/embeddings:/bench:ro" \
        --entrypoint python bleep-backend /bench/embed_bench.py
"""

import os
import resource
import statistics
import time

from bleep.memory.embeddings import LOCAL_THREADS, FastEmbedEmbedder

TEXT = (
    "container died on a/compose:shop. Cause: shop-web was stopped and its restart policy is "
    "no, so nothing brought it back. Action: docker.start shop-web-1 on a: done."
)
threads = int(os.environ["THREADS"]) if os.environ.get("THREADS") else LOCAL_THREADS


def peak_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # KiB on Linux


before = peak_mb()
start = time.perf_counter()
embedder = FastEmbedEmbedder(threads=threads)
embedder.embed(["warm up"])
load_s = time.perf_counter() - start
loaded = peak_mb()

one: list[float] = []
cpu_start = time.process_time()
for _ in range(100):
    start = time.perf_counter()
    embedder.embed([TEXT])
    one.append(time.perf_counter() - start)
cpu_ms = (time.process_time() - cpu_start) / 100 * 1000

batch: list[float] = []
for _ in range(10):
    start = time.perf_counter()
    embedder.embed([TEXT] * 32)
    batch.append(time.perf_counter() - start)

print(f"threads {threads}, dims {embedder.embed([TEXT]).shape[1]}")
print(f"load {load_s:.2f} s")
print(f"one text: median {statistics.median(one) * 1000:.1f} ms, p95 {sorted(one)[94] * 1000:.1f} ms")
print(f"one text: {cpu_ms:.1f} ms of CPU")
print(f"batch of 32: median {statistics.median(batch) * 1000:.0f} ms")
print(f"peak memory: {before:.0f} MB before, {loaded:.0f} MB loaded, {peak_mb():.0f} MB at the end")
