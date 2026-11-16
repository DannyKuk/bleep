import { afterEach, describe, expect, it, vi } from "vitest";

import { loginIsOff } from "@/lib/login-off";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("loginIsOff", () => {
  it("is true when /api/auth/me answers without a session", async () => {
    const fetchMock = vi.fn(async () => Response.json({ authenticated: true }));
    vi.stubGlobal("fetch", fetchMock);
    expect(await loginIsOff("http://backend:8000")).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith("http://backend:8000/api/auth/me", {
      cache: "no-store",
      signal: expect.any(AbortSignal),
    });
  });

  it("is false when the backend asks for a session", async () => {
    vi.stubGlobal("fetch", async () =>
      Response.json({ detail: "not authenticated" }, { status: 401 }),
    );
    expect(await loginIsOff("http://backend:8000")).toBe(false);
  });

  it("is false when the backend cannot be reached, so the form still shows", async () => {
    vi.stubGlobal("fetch", async () => {
      throw new TypeError("fetch failed");
    });
    expect(await loginIsOff("http://backend:8000")).toBe(false);
  });

  it("gives up on a backend that never answers after 2 s, so /login does not hang (#626)", async () => {
    // A fetch that only ends when its signal aborts; the timeout is spied on so no test waits 2 s.
    const timeout = vi.spyOn(AbortSignal, "timeout").mockReturnValue(AbortSignal.abort());
    vi.stubGlobal(
      "fetch",
      (_url: string, init: RequestInit) =>
        new Promise((_resolve, reject) => {
          init.signal?.addEventListener("abort", () => reject(init.signal?.reason));
          if (init.signal?.aborted) reject(init.signal.reason);
        }),
    );

    expect(await loginIsOff("http://backend:8000")).toBe(false);
    expect(timeout).toHaveBeenCalledWith(2000);
  });
});
