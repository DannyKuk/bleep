"""The demo worker: every few seconds it processes a batch of the shop's uploads, holding the
batch in memory.

WORKER_BATCH_MB is the batch size. The container's memory limit is 64 MB.
"""

import logging
import os
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s worker: %(message)s")
log = logging.getLogger("worker")

BATCH_MB = int(os.environ.get("WORKER_BATCH_MB", "8"))
UPLOADS = Path(os.environ.get("UPLOADS_DIR", "/uploads"))
CHUNK_MB = 4
INTERVAL_S = 10


def process(number: int) -> None:
    held: list[bytes] = []
    for _ in range(0, BATCH_MB, CHUNK_MB):
        held.append(b"x" * CHUNK_MB * 1024 * 1024)
        if len(held) % 4 == 0:
            log.info("batch %d: %d MB loaded", number, len(held) * CHUNK_MB)
    waiting = sum(1 for p in UPLOADS.iterdir() if not p.name.startswith("."))
    log.info(
        "batch %d done: %d MB processed, %d uploads waiting", number, len(held) * CHUNK_MB, waiting
    )


def main() -> None:
    log.info("worker started, batch size %d MB", BATCH_MB)
    number = 0
    while True:
        number += 1
        process(number)
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    main()
