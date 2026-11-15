import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DOUBT_MS, LoginForm } from "@/components/login-form";
import { setLanguage } from "@/lib/i18n";

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

function mood(): string | null {
  return document.querySelector("svg[data-mood]")?.getAttribute("data-mood") ?? null;
}

describe("Bleep on the login page", () => {
  beforeEach(() => {
    replace.mockReset();
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => vi.useRealTimers());

  it("rests, works while the login is checked, and doubts a wrong password for a moment", async () => {
    let answer: (response: Response) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>((resolve) => (answer = resolve))),
    );
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<LoginForm next="/" />);
    expect(mood()).toBe("resting");

    await user.type(screen.getByLabelText(/password/i), "pw");
    await user.click(screen.getByRole("button", { name: /log in/i }));
    expect(mood()).toBe("working");

    await act(async () =>
      answer(
        new Response(JSON.stringify({ detail: "wrong password" }), {
          status: 401,
          headers: { "content-type": "application/json" },
        }),
      ),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("wrong password");
    expect(mood()).toBe("not_sure");

    act(() => vi.advanceTimersByTime(DOUBT_MS));
    expect(mood()).toBe("resting");
    expect(screen.getByRole("alert")).toHaveTextContent("wrong password");
  });

  it("rests again as soon as the person types", async () => {
    reply(401, { detail: "wrong password" });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<LoginForm next="/" />);
    await user.type(screen.getByLabelText(/password/i), "pw");
    await user.click(screen.getByRole("button", { name: /log in/i }));
    await screen.findByRole("alert");
    expect(mood()).toBe("not_sure");

    await user.type(screen.getByLabelText(/password/i), "x");
    expect(mood()).toBe("resting");
  });
});

describe("LoginForm in German", () => {
  beforeEach(() => {
    replace.mockReset();
    act(() => setLanguage("de"));
  });

  it("shows the last language the page knew, without a settings read", async () => {
    reply(401, { detail: "wrong password" });
    render(<LoginForm next="/" />);
    expect(screen.getByLabelText("Passwort")).toBeInTheDocument();
    expect(document.title).toBe("Anmelden · Bleep");
    await userEvent.type(screen.getByLabelText("Passwort"), "pw");
    await userEvent.click(screen.getByRole("button", { name: "Anmelden" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("falsches Passwort");
  });

  it("says the wait time on 429 and that the server is out of reach", async () => {
    reply(429, { detail: "too many attempts" }, { "retry-after": "30" });
    const { unmount } = render(<LoginForm next="/" />);
    await userEvent.type(screen.getByLabelText("Passwort"), "pw");
    await userEvent.click(screen.getByRole("button", { name: "Anmelden" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "zu viele Versuche, versuch es in 30 s noch mal",
    );
    unmount();

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Promise.reject(new TypeError("offline"))),
    );
    render(<LoginForm next="/" />);
    await userEvent.type(screen.getByLabelText("Passwort"), "pw");
    await userEvent.click(screen.getByRole("button", { name: "Anmelden" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Server nicht erreichbar");
  });
});
