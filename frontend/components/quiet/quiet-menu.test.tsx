import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { QuietMenu } from "@/components/quiet/quiet-menu";
import { json, sentBody, settingsView, stubBackend } from "@/components/settings/fixtures";
import { PreferencesProvider, useTimeZone } from "@/components/preferences";
import type { QuietView } from "@/lib/api-types";
import { morningLabel, snoozeUntil } from "@/lib/quiet-api";

const service = { scope: "service", id: "demo/compose:shop", name: "shop" } as const;
const PATH = "/api/quiet/services/demo/compose%3Ashop";
const since = "2026-11-04T15:00:00Z";
const view: QuietView = { scope: "service", mode: "leave_alone", until: null, since };

afterEach(() => vi.useRealTimers());

const openMenu = () => userEvent.click(screen.getByRole("button", { name: "Quiet shop" }));
const item = (name: string | RegExp) => screen.findByRole("menuitem", { name });

describe("QuietMenu", () => {
  it("names what it silences when nothing is quiet yet", async () => {
    render(<QuietMenu target={service} quiet={null} onChanged={() => {}} />);
    await openMenu();
    expect(await screen.findByText("Snooze shop")).toBeInTheDocument();
  });

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

function ZoneShown() {
  return <p>zone: {useTimeZone() ?? "browser"}</p>;
}

describe("QuietMenu in Bleep's time zone", () => {
  /** The menu under the app's time zone, read from the settings, at noon UTC on 9 Nov 2026. */
  async function inZone(timezone: string | null) {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-09T12:00:00Z"));
    const fetchMock = stubBackend({
      "GET /api/settings": json(settingsView({ timezone })),
      [`PUT ${PATH}`]: json({ ...view, mode: "snooze", until: "x" }),
    });
    render(
      <PreferencesProvider>
        <ZoneShown />
        <QuietMenu target={service} quiet={null} onChanged={() => {}} />
      </PreferencesProvider>,
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/settings", expect.anything()));
    await screen.findByText(`zone: ${timezone ?? "browser"}`);
    return fetchMock;
  }

  it("makes until 8:00 the next 08:00 in the chosen zone, not the browser's", async () => {
    const fetchMock = await inZone("America/New_York"); // 07:00 there
    await openMenu();
    await userEvent.click(await item("Until 8:00"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(sentBody(fetchMock, "PUT").until).toBe("2026-11-09T13:00:00.000Z");
  });

  it("uses the browser's own zone when none is chosen", async () => {
    const fetchMock = await inZone(null);
    await openMenu();
    const morning = snoozeUntil("morning", new Date());
    await userEvent.click(await item(morningLabel(new Date(), undefined, "en")));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(sentBody(fetchMock, "PUT").until).toBe(morning.toISOString());
    expect(morning.getHours()).toBe(8);
  });
});
