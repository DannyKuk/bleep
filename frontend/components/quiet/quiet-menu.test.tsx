import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { QuietMenu } from "@/components/quiet/quiet-menu";
import { json, sentBody, stubBackend } from "@/components/settings/fixtures";
import type { QuietView } from "@/lib/api-types";

const service = { scope: "service", id: "demo/compose:shop", name: "shop" } as const;
const PATH = "/api/quiet/services/demo/compose%3Ashop";
const since = "2026-11-04T15:00:00Z";
const view: QuietView = { scope: "service", mode: "leave_alone", until: null, since };

afterEach(() => vi.useRealTimers());

const openMenu = () => userEvent.click(screen.getByRole("button", { name: "Quiet shop" }));
const item = (name: string | RegExp) => screen.findByRole("menuitem", { name });

describe("QuietMenu", () => {
  it("choosing For 4 hours PUTs a snooze with until four hours ahead and calls onChanged", async () => {
    const fetchMock = stubBackend({
      [`PUT ${PATH}`]: json({ ...view, mode: "snooze", until: "x" }),
    });
    const onChanged = vi.fn();
    render(<QuietMenu target={service} quiet={null} onChanged={onChanged} />);
    const before = Date.now();
    await openMenu();
    await userEvent.click(await item("For 4 hours"));

    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
    const body = sentBody(fetchMock, "PUT");
    expect(body.mode).toBe("snooze");
    const ahead = Date.parse(body.until) - before;
    expect(ahead).toBeGreaterThanOrEqual(4 * 3600_000);
    expect(ahead).toBeLessThan(4 * 3600_000 + 5000);
  });

  it("Leave it alone PUTs leave_alone without until", async () => {
    const fetchMock = stubBackend({ [`PUT ${PATH}`]: json(view) });
    render(<QuietMenu target={service} quiet={null} onChanged={() => {}} />);
    await openMenu();
    await userEvent.click(await item(/Leave it alone/));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(sentBody(fetchMock, "PUT")).toEqual({ mode: "leave_alone" });
  });

  it("a quiet service's menu offers Watch again, which DELETEs", async () => {
    const fetchMock = stubBackend({ [`DELETE ${PATH}`]: new Response(null, { status: 204 }) });
    const onChanged = vi.fn();
    render(<QuietMenu target={service} quiet={view} onChanged={onChanged} />);
    await openMenu();
    expect(await screen.findByText("Left alone")).toBeInTheDocument();
    await userEvent.click(await item("Watch again"));
    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
    expect(fetchMock.mock.calls[0][1]?.method).toBe("DELETE");
  });

  it("a service quiet through its host offers no Watch again for the host", async () => {
    render(<QuietMenu target={service} quiet={{ ...view, scope: "host" }} onChanged={() => {}} />);
    await openMenu();
    expect(await screen.findByText(/Host left alone/)).toHaveTextContent("set on its host");
    expect(screen.queryByRole("menuitem", { name: "Watch again" })).toBeNull();
  });

  it("a failed PUT shows the API's words and keeps the menu usable", async () => {
    const fetchMock = stubBackend({
      [`PUT ${PATH}`]: [json({ detail: "no such service" }, 404), json(view)],
    });
    const onChanged = vi.fn();
    render(<QuietMenu target={service} quiet={null} onChanged={onChanged} />);
    await openMenu();
    await userEvent.click(await item("For 1 hour"));
    expect(await screen.findByRole("alert")).toHaveTextContent("no such service");
    expect(onChanged).not.toHaveBeenCalled();

    await userEvent.click(await item("For 1 hour"));
    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("ignores a second choice while the first is in flight", async () => {
    let release: (reply: Response) => void = () => {};
    const fetchMock = vi.fn<typeof fetch>(
      () => new Promise<Response>((resolve) => (release = resolve)),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<QuietMenu target={service} quiet={null} onChanged={() => {}} />);
    await openMenu();
    const choice = await item("For 1 hour");
    await userEvent.dblClick(choice);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    release(json(view));
  });
});
