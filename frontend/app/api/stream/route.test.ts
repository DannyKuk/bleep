// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

import { GET } from "@/app/api/stream/route";

function request(headers: Record<string, string> = {}) {
  return new Request("http://dashboard/api/stream?incident_id=inc-1&after=4", { headers });
}

afterEach(() => vi.unstubAllGlobals());

describe("GET /api/stream", () => {
  it("pipes the backend stream with the cursor, the session and no compression", async () => {
    const fetchMock = vi.fn<typeof fetch>(
      async () =>
        new Response("id: 5\ndata: {}\n\n", {
          headers: { "content-type": "text/event-stream; charset=utf-8" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET(
      request({
        cookie: "bleep_session=abc",
        "last-event-id": "4",
        "x-forwarded-for": "10.0.0.9",
      }),
    );

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8000/api/stream?incident_id=inc-1&after=4");
    const sent = new Headers(init?.headers);
    expect(sent.get("cookie")).toBe("bleep_session=abc");
    expect(sent.get("last-event-id")).toBe("4");
    expect(sent.get("x-forwarded-for")).toBeNull();
    expect(response.headers.get("content-type")).toContain("text/event-stream");
    expect(response.headers.get("cache-control")).toContain("no-transform");
    expect(await response.text()).toBe("id: 5\ndata: {}\n\n");
  });

  it("passes a refusal through with its status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ detail: "not signed in" }, { status: 401 })),
    );
    const response = await GET(request());
    expect(response.status).toBe(401);
    expect(await response.json()).toEqual({ detail: "not signed in" });
  });

  it("answers 502 when the backend is down", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("fetch failed");
      }),
    );
    const response = await GET(request());
    expect(response.status).toBe(502);
  });
});
