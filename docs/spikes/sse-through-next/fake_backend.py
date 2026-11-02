"""A stand-in for the backend's /api/stream: sse-starlette, `id:` = seq, Last-Event-ID replay.

Sends `count` events one second apart, starting after the cursor, and logs when a client goes
away. `?no_transform=1` adds `Cache-Control: no-store, no-transform`. Run from backend/ with:
uv run uvicorn --app-dir ../docs/spikes/sse-through-next fake_backend:app --port 8765
"""

import asyncio
import json
import time

from fastapi import FastAPI, Header
from sse_starlette import EventSourceResponse, ServerSentEvent

app = FastAPI()


@app.get("/api/stream")
async def stream(
    incident_id: str = "x",
    after: int = 0,
    count: int = 5,
    no_transform: bool = False,
    last_event_id: int | None = Header(None),
    cookie: str | None = Header(None),
) -> EventSourceResponse:
    cursor = last_event_id if last_event_id is not None else after
    print(f"open: cursor={cursor} cookie={cookie!r}", flush=True)

    async def messages():
        try:
            for seq in range(cursor + 1, cursor + 1 + count):
                await asyncio.sleep(1)
                body = {
                    "incident_id": incident_id,
                    "seq": seq,
                    "kind": "notice",
                    "payload": {"sent": time.time()},
                    "at": "2026-11-02T00:00:00Z",
                }
                yield ServerSentEvent(data=json.dumps(body), id=str(seq))
        finally:
            print(f"closed: incident={incident_id}", flush=True)

    headers = {"Cache-Control": "no-store, no-transform"} if no_transform else None
    return EventSourceResponse(messages(), ping=15, headers=headers)
