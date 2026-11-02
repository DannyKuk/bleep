// The live event stream, piped from the backend. A route handler, not the /api rewrite: through
// the rewrite Next gzips text/event-stream and holds every event back until the stream ends.
// `no-transform` keeps the compression off this response. See docs/decisions/sse-through-next.md.

export const dynamic = "force-dynamic";

const BACKEND = process.env.BACKEND_URL ?? "http://localhost:8000";

// Only what the backend needs: the session, the browser's reconnect cursor, the media type.
const FORWARDED = ["cookie", "last-event-id", "accept"];

export async function GET(request: Request): Promise<Response> {
  const { search } = new URL(request.url);
  const headers = new Headers();
  for (const name of FORWARDED) {
    const value = request.headers.get(name);
    if (value !== null) headers.set(name, value);
  }

  let upstream: Response;
  try {
    // Aborting with the browser's request closes the backend stream, which unsubscribes it.
    upstream = await fetch(`${BACKEND}/api/stream${search}`, {
      headers,
      signal: request.signal,
      cache: "no-store",
    });
  } catch {
    return Response.json({ detail: "The backend is not reachable" }, { status: 502 });
  }

  if (!upstream.ok || upstream.body === null) {
    // 401 and 404 pass through with their JSON detail, as the rewrite would send them.
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
    });
  }
  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache, no-transform",
      "x-accel-buffering": "no",
    },
  });
}
