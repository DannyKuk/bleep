import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { incident } from "@/components/incident/fixtures";
import { ServicePage } from "@/components/service/service-page";
import type { ServiceDetail } from "@/lib/api-types";

const overall = vi.hoisted(() => ({ hosts: [], refresh: vi.fn() }));
vi.mock("@/components/overall-state", () => ({ useOverall: () => overall }));

const SERVICE: ServiceDetail = {
  service: {
    id: "demo/compose:shop",
    host_id: "demo",
    name: "shop",
    source: "compose",
    health: "ok",
    containers: [
      {
        id: "c1",
        name: "shop-web-1",
        image: "shop:1",
        state: "running",
        health: "none",
        restart_count: 0,
        exit_code: null,
        protected: false,
      },
    ],
    urls: [
      {
        url: "http://localhost:8080/",
        status: "ok",
        last_checked: null,
        status_code: 200,
        latency_ms: 12,
      },
    ],
    open_incident_id: null,
    quiet: null,
  },
  rules: ["The shop db is shared with billing."],
  host_rules: ["Never restart postgres without asking."],
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function stubBackend(service: Response) {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input);
    if (url.endsWith("/facts")) return json([]);
    if (url.startsWith("/api/incidents")) return json([incident({ status: "closed" })]);
    return service.clone();
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("ServicePage", () => {
  it("lists the service's URLs, rules, facts and past incidents", async () => {
    const fetchMock = stubBackend(json(SERVICE));
    render(<ServicePage id="demo/compose:shop" />);

    expect(await screen.findByRole("heading", { name: "shop" })).toBeInTheDocument();
    const crumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(crumbs).getByRole("link", { name: "Home" })).toHaveAttribute("href", "/");
    expect(within(crumbs).getByText("demo")).toBeInTheDocument();
    expect(within(crumbs).getByText("shop")).toHaveAttribute("aria-current", "page");
    const urls = screen.getByRole("list", { name: "URLs" });
    expect(urls).toHaveTextContent("http://localhost:8080/");
    const rules = screen.getByRole("region", { name: "Rules I follow" });
    expect(within(rules).getByRole("list", { name: "Rules: This service" })).toHaveTextContent(
      "The shop db is shared with billing.",
    );
    expect(within(rules).getByRole("list", { name: "Rules: Its host" })).toHaveTextContent(
      "Never restart postgres without asking.",
    );
    expect(await screen.findByText(/No facts yet/)).toBeInTheDocument();
    const past = await screen.findByRole("list", { name: "Incidents" });
    expect(past).toHaveTextContent("shop-web-1 died");

    const urlsRead = fetchMock.mock.calls.map(([input]) => String(input));
    expect(urlsRead).toContain("/api/services/demo%2Fcompose%3Ashop");
    expect(urlsRead).toContain("/api/incidents?service_id=demo%2Fcompose%3Ashop&limit=20");
  });

  it("still shows the memory and the incidents of a service Bleep no longer sees", async () => {
    stubBackend(json({ detail: "no such service" }, 404));
    render(<ServicePage id="demo/compose:gone" />);

    expect(await screen.findByText(/does not see this service/)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "What I know about gone" })).toBeInTheDocument();
    expect(await screen.findByRole("list", { name: "Incidents" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Rules I follow" })).not.toBeInTheDocument();
  });

  const QUIET_PATH = "/api/quiet/services/demo/compose%3Ashop";
  /** An ISO string with the local offset, for a local wall-clock time `daysAgo` days back. */
  function localIso(daysAgo: number, hours = 14, minutes = 2): string {
    const at = new Date();
    at.setDate(at.getDate() - daysAgo);
    at.setHours(hours, minutes, 0, 0);
    const offset = -at.getTimezoneOffset();
    const pad = (n: number) => String(Math.trunc(Math.abs(n))).padStart(2, "0");
    const zone = `${offset < 0 ? "-" : "+"}${pad(offset / 60)}:${pad(offset % 60)}`;
    const local = new Date(at.getTime() + offset * 60_000).toISOString().slice(0, 19);
    return local + zone;
  }
  const leftAloneSince = (since: string) => ({
    scope: "service",
    mode: "leave_alone",
    until: null,
    since,
  });
  const own = leftAloneSince(localIso(0));

  function quietService(quiet: unknown) {
    return json({ ...SERVICE, service: { ...SERVICE.service, quiet } });
  }

  /** The health badge beside the title, if there is one. */
  const titleBadge = () =>
    screen.getByRole("heading", { level: 1 }).parentElement!.querySelector("[data-health]");

  /** Opens the quiet pill's menu and picks Watch again. */
  async function watchAgain() {
    await userEvent.click(await screen.findByRole("button", { name: /^Quiet shop: Left alone/ }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Watch again" }));
  }

  it("shows the health badge and a Quiet button, or the quiet pill as the one control", async () => {
    stubBackend(quietService(null));
    const { unmount } = render(<ServicePage id="demo/compose:shop" />);
    expect(await screen.findByRole("button", { name: "Quiet shop" })).toBeInTheDocument();
    expect(titleBadge()).not.toBeNull();
    expect(screen.queryByRole("note")).toBeNull();
    unmount();

    stubBackend(quietService(own));
    render(<ServicePage id="demo/compose:shop" />);
    expect(
      await screen.findByRole("button", { name: "Quiet shop: Left alone" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Quiet shop" })).toBeNull();
    expect(titleBadge()).toBeNull();
    expect(screen.queryByRole("button", { name: "Watch again" })).toBeNull();
    expect(screen.getByRole("note")).toHaveTextContent(
      "Since 14:02 Bleep opens no incidents for it.",
    );
  });

  it("Watch again from the pill's menu DELETEs and reloads", async () => {
    let quiet: unknown = own;
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (init?.method === "DELETE") {
        quiet = null;
        return new Response(null, { status: 204 });
      }
      if (url.endsWith("/facts")) return json([]);
      if (url.startsWith("/api/incidents")) return json([]);
      return quietService(quiet);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ServicePage id="demo/compose:shop" />);
    await watchAgain();
    await waitFor(() => expect(screen.queryByRole("note")).toBeNull());
    expect(screen.getByRole("button", { name: "Quiet shop" })).toBeInTheDocument();
    const deletes = fetchMock.mock.calls.filter(([, init]) => init?.method === "DELETE");
    expect(String(deletes[0][0])).toBe(QUIET_PATH);
  });

  it.each([
    [0, /^Since 14:02 Bleep/],
    [1, /^Since yesterday 14:02 Bleep/],
    [5, /^Since \w{3} 14:02 Bleep/],
  ])("the note says when it began, %i days ago", async (daysAgo, words) => {
    stubBackend(quietService(leftAloneSince(localIso(daysAgo))));
    render(<ServicePage id="demo/compose:shop" />);
    expect((await screen.findByRole("note")).textContent).toMatch(words);
  });

  it("a failed Watch again says why in the menu and keeps the pill", async () => {
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (init?.method === "DELETE") return json({ detail: "The store is busy." }, 500);
      if (url.endsWith("/facts")) return json([]);
      if (url.startsWith("/api/incidents")) return json([]);
      return quietService(own);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ServicePage id="demo/compose:shop" />);
    await watchAgain();
    expect(await screen.findByRole("alert")).toHaveTextContent("The store is busy.");
    expect(screen.getByRole("note")).toBeInTheDocument();
  });

  it("a quiet choice reads the incidents and the header again", async () => {
    let quiet: unknown = own;
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (init?.method === "DELETE") {
        quiet = null;
        return new Response(null, { status: 204 });
      }
      if (url.endsWith("/facts")) return json([]);
      if (url.startsWith("/api/incidents")) return json([]);
      return quietService(quiet);
    });
    vi.stubGlobal("fetch", fetchMock);
    overall.refresh.mockClear();
    render(<ServicePage id="demo/compose:shop" />);
    const incidentReads = () =>
      fetchMock.mock.calls.filter(([input]) => String(input).startsWith("/api/incidents")).length;
    await watchAgain();
    await waitFor(() => expect(incidentReads()).toBe(2));
    expect(overall.refresh).toHaveBeenCalled();
  });

  it("a snooze says until when", async () => {
    const until = new Date(2030, 0, 1, 18, 0).toISOString();
    stubBackend(quietService({ ...own, mode: "snooze", until }));
    render(<ServicePage id="demo/compose:shop" />);
    expect((await screen.findByRole("note")).textContent).toMatch(
      /^Bleep opens no incidents for it until .*18:00\.$/,
    );
  });

  it("a service quiet through its host says so and offers no Watch again", async () => {
    stubBackend(quietService({ ...own, scope: "host" }));
    render(<ServicePage id="demo/compose:shop" />);
    expect(await screen.findByRole("note")).toHaveTextContent(
      "Its host is left alone, so Bleep opens no incidents for it.",
    );
    await userEvent.click(screen.getByRole("button", { name: /^Quiet shop: Host left alone/ }));
    expect(await screen.findByRole("menu")).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Watch again" })).toBeNull();
  });
});
