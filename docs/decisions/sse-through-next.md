# The live stream through Next.js: a route handler, not the rewrite

> **Answer:** the dashboard serves `/api/stream` from a route handler (`frontend/app/api/stream/route.ts`) that pipes the backend's response with `Cache-Control: no-cache, no-transform`. Every other `/api` path still goes through the rewrite. Through the rewrite, Next.js 16.4.4 gzips `text/event-stream` whenever the browser asks for compression, and every browser does. Gzip held every event back until the stream ended: five events sent one second apart all arrived at 5.02 s, under both `next start` and the standalone `server.js` the Docker image runs. Through the route handler they arrived at 1, 2, 3, 4 and 5 s, uncompressed, with `Last-Event-ID` and the cookie passed on, and closing the browser's request closed the backend's stream.

## Question

The dashboard reaches the backend through a Next.js rewrite of `/api/:path*`, so the session cookie stays same-origin ([app design](../design/app.md)). Does a server-sent event stream go through that rewrite as it is sent, in a production build, not only under `next dev`?

## What we tried

A stand-in backend ([`fake_backend.py`](../spikes/sse-through-next/fake_backend.py)) streams the way `api/stream.py` does: sse-starlette, `id:` = seq, replay after `Last-Event-ID`, five events one second apart. [`timed.py`](../spikes/sse-through-next/timed.py) prints when each event arrives, asking for gzip as a browser does (`--gzip`) or not. Each was run against:

- the backend directly;
- `next build && next start` with the rewrite (`BACKEND_URL` pointing at the stand-in);
- the same build's standalone `node .next/standalone/server.js`, which is what the Docker image runs;
- the backend sending `Cache-Control: no-store, no-transform` (sse-starlette's default is `no-store`), through the rewrite;
- a route handler at `app/api/stream/route.ts` that forwards `cookie`, `last-event-id` and `accept`, and returns the upstream body with `no-cache, no-transform`.

Last, the real backend app (`create_app`) over a temporary database, with a scripted incident on the bus in place of a model run: logged in through the dashboard, then read the incident's stream through `next start` with gzip asked.

## Result

Seconds from the request until each event arrived (Next.js 16.4.4, Node 22.23, sse-starlette 3.6.0, macOS):

| Path | Gzip asked | `Content-Encoding` | Events 8 to 12 arrived at |
|---|---|---|---|
| Backend directly | yes | none | 1.01, 2.01, 3.01, 4.01, 5.01 |
| `next start`, rewrite | no | none | 1.01, 2.01, 3.01, 4.01, 5.01 |
| `next start`, rewrite | yes | gzip | 5.02 for all five |
| standalone `server.js`, rewrite | yes | gzip | 5.01 for all five |
| `next start`, rewrite, backend sends `no-transform` | yes | none | 1.02, 2.02, 3.02, 4.02, 5.02 |
| `next start`, route handler | yes | none | 1.04, 2.03, 3.03, 4.04, 5.04 |
| standalone `server.js`, route handler | yes | none | 1.03, 2.03, 3.03, 4.03, 5.03 |

- With `Last-Event-ID: 7` sent, every path started at seq 8, so the header reaches the backend both through the rewrite and through the route handler. The stand-in logged the forwarded cookie.
- A client that hung up after 3 s of a 60-event stream: the stand-in logged its stream closed then, for `next start` and for the standalone server. The route handler passes the browser's abort on to its upstream fetch.
- Other `/api` paths still go to the backend through the rewrite (`/api/hosts` answered with the backend's `server: uvicorn` header).
- The real backend through `next start`: login 204, then the stream answered with no `Content-Encoding` and `no-cache, no-transform`, sent the incident's history at once and the scripted events one by one as they were published.

## What it shows

- **The rewrite is fine for JSON and wrong for a stream.** Next's compression buffers inside gzip until the response ends; an incident's stream may stay open for hours.
- **`no-transform` is what turns compression off.** Next's compression skips a response that says `no-transform`. The backend could send it instead, and that was measured to work through the rewrite too. The route handler was chosen because the dashboard then does not depend on a header the backend has to remember, and it states in the frontend code why this path is special.
- **The route handler needs the backend address at runtime,** so `next.config.ts` puts `BACKEND_URL` into `env`, which Next inlines at build time. It is the same build argument the rewrite already uses, so the Docker image needs nothing new.

## Limits

- Measured on one machine over loopback. A proxy in front of the dashboard (none today; it is LAN-only) could buffer again; sse-starlette's `X-Accel-Buffering: no` is passed on for nginx.
- The route handler forwards only `cookie`, `last-event-id` and `accept`. A future header the backend's stream needs has to be added to its list.
- Measured with `curl`-like clients (Python `urllib`), not a browser's `EventSource`; the request headers that matter (`Accept-Encoding`, `Last-Event-ID`, the cookie) were set the way a browser sets them.

## Reproduce

```bash
cd backend && uv run uvicorn --app-dir ../docs/spikes/sse-through-next fake_backend:app --port 8765 &
cd ../frontend && BACKEND_URL=http://localhost:8765 npm run build
npx next start -p 3123 &
PORT=3124 HOSTNAME=127.0.0.1 node .next/standalone/server.js &
python3 ../docs/spikes/sse-through-next/timed.py "http://localhost:3123/api/stream?incident_id=x" --gzip
python3 ../docs/spikes/sse-through-next/timed.py "http://127.0.0.1:3124/api/stream?incident_id=x" --gzip
```

To see the buffering, delete `frontend/app/api/stream/`, rebuild and run the same two lines.
