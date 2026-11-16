import { afterEach, describe, expect, it, vi } from "vitest";

import { loginIsOff } from "@/lib/login-off";

afterEach(() => vi.unstubAllGlobals());

describe("loginIsOff", () => {
  it("is true when /api/auth/me answers without a session", async () => {
    const fetchMock = vi.fn(async () => Response.json({ authenticated: true }));
    vi.stubGlobal("fetch", fetchMock);
    expect(await loginIsOff("http://backend:8000")).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith("http://backend:8000/api/auth/me", {
      cache: "no-store",
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
});
