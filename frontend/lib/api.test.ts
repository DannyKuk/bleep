import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiFetch } from "@/lib/api";

function stubLocation(pathname: string, search = "") {
  const assign = vi.fn();
  vi.stubGlobal("location", { pathname, search, assign });
  return assign;
}

function reply(status: number, body?: unknown, headers: Record<string, string> = {}) {
  const fetchMock = vi.fn(async () =>
    status === 204
      ? new Response(null, { status })
      : new Response(JSON.stringify(body ?? {}), {
          status,
          headers: { "content-type": "application/json", ...headers },
        }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

describe("apiFetch", () => {
  it("returns parsed JSON and sends same-origin credentials", async () => {
    const fetchMock = reply(200, [{ id: "demo" }]);
    await expect(apiFetch("/api/hosts")).resolves.toEqual([{ id: "demo" }]);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/hosts",
      expect.objectContaining({ credentials: "same-origin" }),
    );
  });

  it("returns undefined on 204", async () => {
    reply(204);
    await expect(apiFetch("/api/auth/logout", { method: "POST" })).resolves.toBeUndefined();
  });

  it("redirects to /login?next=<current path> on 401", async () => {
    reply(401, { detail: "not authenticated" });
    const assign = stubLocation("/incidents", "?open=1");
    await expect(apiFetch("/api/hosts")).rejects.toMatchObject({ status: 401 });
    expect(assign).toHaveBeenCalledWith("/login?next=%2Fincidents%3Fopen%3D1");
  });

  it("does not redirect on 401 when already on /login", async () => {
    reply(401, { detail: "wrong password" });
    const assign = stubLocation("/login");
    await expect(apiFetch("/api/auth/login", { method: "POST" })).rejects.toBeInstanceOf(ApiError);
    expect(assign).not.toHaveBeenCalled();
  });

  it("throws a typed error with the detail and Retry-After on 429", async () => {
    reply(429, { detail: "too many attempts" }, { "retry-after": "42" });
    const error = await apiFetch("/api/auth/login", { method: "POST" }).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 429, detail: "too many attempts", retryAfter: 42 });
  });
});
