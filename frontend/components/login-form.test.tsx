import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "@/components/login-form";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

function reply(status: number, body?: unknown, headers: Record<string, string> = {}) {
  const fetchMock = vi.fn(async () =>
    status === 204
      ? new Response(null, { status })
      : new Response(JSON.stringify(body), {
          status,
          headers: { "content-type": "application/json", ...headers },
        }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function submit(password = "pw") {
  await userEvent.type(screen.getByLabelText(/password/i), password);
  await userEvent.click(screen.getByRole("button", { name: /log in|sign in/i }));
}

describe("LoginForm", () => {
  beforeEach(() => replace.mockReset());

  it("shows 'wrong password' on 401", async () => {
    reply(401, { detail: "wrong password" });
    render(<LoginForm next="/" />);
    await submit();
    expect(await screen.findByRole("alert")).toHaveTextContent("wrong password");
    expect(replace).not.toHaveBeenCalled();
  });

  it("shows the wait time on 429", async () => {
    reply(429, { detail: "too many attempts" }, { "retry-after": "30" });
    render(<LoginForm next="/" />);
    await submit();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "too many attempts, try again in 30 s",
    );
  });

  it("goes to next on success", async () => {
    const fetchMock = reply(204);
    render(<LoginForm next="/incidents" />);
    await submit("secret");
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/incidents"));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/auth/login",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ password: "secret" }) }),
    );
  });

  it("ignores an off-site next", async () => {
    reply(204);
    render(<LoginForm next="//evil.test" />);
    await submit();
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
  });
});
