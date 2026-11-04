"""Refuse an oversized write body by its size, before anything reads it.

FastAPI buffers and parses a whole body before a route runs, so a check on the parsed JSON
(`check_size`) cannot stop a client from making the backend hold a huge one. This middleware looks
at `Content-Length` and, for a chunked body, stops reading at the cap. It covers only the settings,
plugin and prompt-override writes, the routes that read free-form JSON.
"""

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from bleep.api.settings import MAX_BODY_CHARS

# A character is at most 4 bytes in UTF-8, so a compact JSON body (what `JSON.stringify` sends)
# within the character limit is never refused here. Whitespace or \\uXXXX escapes can make the raw
# body larger than that and it is refused. `check_size` applies the exact character limit.
MAX_BODY_BYTES = MAX_BODY_CHARS * 4

LIMITED_PREFIXES = ("/api/plugins/", "/api/dev/prompts/")
DISCONNECTED = object()  # the client left mid-body: there is nobody to answer
LIMITED_METHODS = frozenset({"PUT", "POST", "PATCH"})


def is_limited(scope: Scope) -> bool:
    path = scope["path"]
    return scope["method"] in LIMITED_METHODS and (
        path == "/api/settings" or path.startswith(LIMITED_PREFIXES)
    )


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_limited(scope):
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and _too_big(declared, self.max_bytes):
            await self._refuse(send)
            return
        body = await self._read_capped(receive)
        if body is DISCONNECTED:
            return
        if body is None:
            await self._refuse(send)
            return
        assert isinstance(body, bytes)
        sent = False

        async def replay() -> Message:
            nonlocal sent
            if sent:
                return await receive()  # the disconnect, once the app is done with the body
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay, send)

    async def _read_capped(self, receive: Receive) -> bytes | None | object:
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return DISCONNECTED
            size += len(message.get("body", b""))
            if size > self.max_bytes:
                return None
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                return b"".join(chunks)

    async def _refuse(self, send: Send) -> None:
        payload = json.dumps(
            {"detail": f"the body is over {self.max_bytes:,} bytes; the body is not accepted"}
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode()),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})


def _too_big(declared: bytes, limit: int) -> bool:
    try:
        return int(declared) > limit
    except ValueError:
        return False
