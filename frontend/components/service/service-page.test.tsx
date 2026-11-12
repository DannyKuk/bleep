import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { incident } from "@/components/incident/fixtures";
import { OverallProvider } from "@/components/overall-state";
import { ServicePage } from "@/components/service/service-page";
import { invalid, sentBody, settingsView } from "@/components/settings/fixtures";
import type { IncidentListItem, ServiceDetail, UrlCheckPoint } from "@/lib/api-types";
import { formatTime } from "@/lib/format";
import { MESSAGES, setLanguage } from "@/lib/i18n";
import type { UrlCheck } from "@/lib/settings-types";

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
    watching: [],
  },
  rules: ["The shop db is shared with billing."],
  host_rules: ["Never restart postgres without asking."],
  health_since: null,
  recent_checks: [{ url: "http://localhost:8080/", checks: [] }],
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** The header's overall poll (`OverallProvider`): the newest incidents, the hosts and the
 * services. */
function answerOverall(input: RequestInfo | URL): Response | undefined {
  const url = String(input);
  if (url === "/api/hosts" || url === "/api/services") return json([]);
  if (url.startsWith("/api/incidents?limit=")) return json([]);
  return undefined;
}

function renderPage(id = "demo/compose:shop") {
  return render(
    <OverallProvider>
      <ServicePage id={id} />
    </OverallProvider>,
  );
}

/** The settings of a Bleep with no host the service is on, so its cards are read-only. */
const NO_SETTINGS = settingsView({ hosts: [], overrides: {} });

function stubBackend(service: Response, incidents: IncidentListItem[] = [pastIncident()]) {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const overallReply = answerOverall(input);
    if (overallReply) return overallReply;
    const url = String(input);
    if (url === "/api/settings") return json(NO_SETTINGS);
    if (url.endsWith("/facts")) return json([]);
    if (url.endsWith("/suggested-checks")) return json([]);
    if (url.startsWith("/api/incidents")) return json(incidents);
    return service.clone();
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const NO_VOTES = { diagnosis: null, proposal: null };

function pastIncident(
  overrides: Partial<IncidentListItem> = {},
  votes: IncidentListItem["votes"] = NO_VOTES,
): IncidentListItem {
  return { ...incident({ status: "closed" }), votes, repeats: 1, kind: "death", ...overrides };
}

describe("ServicePage", () => {
  it("lets a long unbroken service name wrap instead of widening the page", async () => {
    const name = "s".repeat(50);
    stubBackend(json({ ...SERVICE, service: { ...SERVICE.service, name } }));
    renderPage("demo/compose:shop");

    expect(await screen.findByRole("heading", { level: 1, name })).toHaveClass("wrap-anywhere");
    const crumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(crumbs).getByText(name)).toHaveClass("wrap-anywhere");
  });

  it("shows a lock on each container Bleep refuses to act on", async () => {
    const db = { ...SERVICE.service.containers[0], id: "c2", name: "shop-db-1", protected: true };
    const containers = [...SERVICE.service.containers, db];
    stubBackend(json({ ...SERVICE, service: { ...SERVICE.service, containers } }));
    renderPage("demo/compose:shop");

    const list = await screen.findByRole("list", { name: "Containers" });
    const [web, locked] = within(list).getAllByRole("listitem");
    expect(locked).toHaveTextContent("shop-db-1 (protected: Bleep never acts on it)");
    expect(locked.querySelector("svg.lucide-lock")).not.toBeNull();
    expect(web).not.toHaveTextContent("protected");
    expect(web.querySelector("svg.lucide-lock")).toBeNull();
  });

  it("shows a finished job as done", async () => {
    stubBackend(json({ ...SERVICE, service: { ...SERVICE.service, health: "done" } }));
    renderPage("demo/compose:shop");
    await screen.findByRole("heading", { level: 1, name: "shop" });
    expect(await screen.findAllByText("Done")).not.toHaveLength(0);
    expect(screen.queryByText("Down")).toBeNull();
  });

  it("lists the service's URLs, rules, facts and past incidents", async () => {
    const fetchMock = stubBackend(json(SERVICE));
    renderPage("demo/compose:shop");

    expect(await screen.findByRole("heading", { name: "shop" })).toBeInTheDocument();
    const crumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(crumbs).getByRole("link", { name: "Home" })).toHaveAttribute("href", "/");
    expect(within(crumbs).getByRole("link", { name: "demo" })).toHaveAttribute(
      "href",
      "/hosts/demo",
    );
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
    renderPage("demo/compose:gone");

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
    const { unmount } = renderPage("demo/compose:shop");
    expect(await screen.findByRole("button", { name: "Quiet shop" })).toBeInTheDocument();
    expect(titleBadge()).not.toBeNull();
    expect(screen.queryByRole("note")).toBeNull();
    unmount();

    stubBackend(quietService(own));
    renderPage("demo/compose:shop");
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
      const overallReply = answerOverall(input);
      if (overallReply) return overallReply;
      const url = String(input);
      if (init?.method === "DELETE") {
        quiet = null;
        return new Response(null, { status: 204 });
      }
      if (url === "/api/settings") return json(NO_SETTINGS);
      if (url.endsWith("/facts")) return json([]);
      if (url.startsWith("/api/incidents")) return json([]);
      return quietService(quiet);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPage("demo/compose:shop");
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
    renderPage("demo/compose:shop");
    expect((await screen.findByRole("note")).textContent).toMatch(words);
  });

  it("a failed Watch again says why in the menu and keeps the pill", async () => {
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const overallReply = answerOverall(input);
      if (overallReply) return overallReply;
      const url = String(input);
      if (init?.method === "DELETE") return json({ detail: "The store is busy." }, 500);
      if (url === "/api/settings") return json(NO_SETTINGS);
      if (url.endsWith("/facts")) return json([]);
      if (url.startsWith("/api/incidents")) return json([]);
      return quietService(own);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPage("demo/compose:shop");
    await watchAgain();
    expect(await screen.findByRole("alert")).toHaveTextContent("The store is busy.");
    expect(screen.getByRole("note")).toBeInTheDocument();
  });

  it("a quiet choice reads the incidents and the header again", async () => {
    let quiet: unknown = own;
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const overallReply = answerOverall(input);
      if (overallReply) return overallReply;
      const url = String(input);
      if (init?.method === "DELETE") {
        quiet = null;
        return new Response(null, { status: 204 });
      }
      if (url === "/api/settings") return json(NO_SETTINGS);
      if (url.endsWith("/facts")) return json([]);
      if (url.startsWith("/api/incidents")) return json([]);
      return quietService(quiet);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPage("demo/compose:shop");
    const reads = (path: string) =>
      fetchMock.mock.calls.filter(([input]) => String(input).startsWith(path)).length;
    await watchAgain();
    await waitFor(() => expect(reads("/api/incidents?service_id=")).toBe(2));
    await waitFor(() => expect(reads("/api/hosts")).toBe(2));
  });

  it("a snooze says until when", async () => {
    const until = new Date(2030, 0, 1, 18, 0).toISOString();
    stubBackend(quietService({ ...own, mode: "snooze", until }));
    renderPage("demo/compose:shop");
    expect((await screen.findByRole("note")).textContent).toMatch(
      /^Bleep opens no incidents for it until .*18:00\.$/,
    );
  });

  it("a service quiet through its host says so and offers no Watch again", async () => {
    stubBackend(quietService({ ...own, scope: "host" }));
    renderPage("demo/compose:shop");
    expect(await screen.findByRole("note")).toHaveTextContent(
      "Its host is left alone, so Bleep opens no incidents for it.",
    );
    await userEvent.click(screen.getByRole("button", { name: /^Quiet shop: Host left alone/ }));
    expect(await screen.findByRole("menu")).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Watch again" })).toBeNull();
  });
});

const SHOP: ServiceDetail = {
  ...SERVICE,
  recent_checks: [],
  service: {
    ...SERVICE.service,
    id: "m1/compose:shop",
    host_id: "m1",
    urls: [
      {
        url: "https://shop.example.test/health",
        status: "failing",
        last_checked: null,
        status_code: 502,
        latency_ms: 30,
      },
    ],
  },
};

/**
 * Answers the service page's reads for `m1/compose:shop`; `GET /api/settings` and
 * `PUT /api/settings` answer from their queues, the last GET again once the queue is down to it.
 */
function settingsBackend({
  service = json(SHOP),
  reads = [settingsView()],
  puts = [],
  suggested = [],
}: {
  service?: Response;
  reads?: (ReturnType<typeof settingsView> | Response)[];
  puts?: (Response | Promise<Response>)[];
  suggested?: UrlCheck[];
} = {}) {
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const overallReply = answerOverall(input);
    if (overallReply) return overallReply;
    const url = String(input);
    if (url === "/api/settings" && init?.method === "PUT") return puts.shift() ?? json({}, 500);
    if (url === "/api/settings") {
      const next = reads.length > 1 ? reads.shift()! : reads[0];
      return next instanceof Response ? next.clone() : json(next);
    }
    if (url.endsWith("/facts")) return json([]);
    if (url.endsWith("/suggested-checks")) return json(suggested);
    if (url.startsWith("/api/incidents")) return json([]);
    return service.clone();
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const region = (name: string) => screen.findByRole("region", { name });

/** Today at a local wall-clock time, as the ISO string the API sends. */
function todayAt(hours: number, minutes: number): string {
  const at = new Date();
  at.setHours(hours, minutes, 0, 0);
  return at.toISOString();
}

function serviceWith(detail: Partial<ServiceDetail>, health = SERVICE.service.health) {
  return json({ ...SERVICE, ...detail, service: { ...SERVICE.service, health } });
}

/** The line under the title: source, host, containers and, when healthy, since when. */
const headline = async () =>
  (await screen.findByText(/service on/, { selector: "p" })).textContent ?? "";

const CHECKS: UrlCheckPoint[] = [
  { ok: true, status_code: 200, latency_ms: 12, checked_at: "2026-11-05T09:00:00Z" },
  { ok: false, status_code: 500, latency_ms: 30, checked_at: "2026-11-05T09:00:30Z" },
  { ok: false, status_code: null, latency_ms: 10000, checked_at: "2026-11-05T09:01:00Z" },
];

describe("ServicePage short names (#397)", () => {
  it("puts each past incident's short name in front of its title, in mono", async () => {
    const named = pastIncident({ id: "inc-2", short_name: "shop #14", title: "shop-web-1 died" });
    const old = pastIncident({ id: "inc-1", title: "url failed on shop" });
    stubBackend(json(SERVICE), [named, old]);
    renderPage("demo/compose:shop");

    const past = await screen.findByRole("list", { name: "Incidents" });
    const row = within(past).getByRole("link", { name: /shop-web-1 died/ });
    expect(row).toHaveTextContent("shop #14 · shop-web-1 died");
    expect(within(row).getByText("shop #14")).toHaveClass("font-mono", "text-ink-2");
    const plain = within(past).getByRole("link", { name: /url failed on shop/ });
    expect(within(plain).getByText("url failed on shop")).toHaveTextContent(/^url failed on shop$/);
    expect(plain).not.toHaveTextContent("·");
  });

  it("names the last incident by its short name when the service is still down", async () => {
    const down = { ...SERVICE.service, health: "down" as const };
    const last = pastIncident({ id: "inc-9", short_name: "shop #9", title: "url failed on shop" });
    stubBackend(json({ ...SERVICE, service: down }), [last]);
    renderPage("demo/compose:shop");

    const note = await screen.findByRole("note", { name: "Still failing" });
    expect(within(note).getByRole("link")).toHaveTextContent(/^shop #9 · url failed on shop$/);
  });
});

describe("ServicePage history", () => {
  it("says a service is still down after its last incident ended, and links that incident", async () => {
    const urls = [{ ...SERVICE.service.urls[0], status: "failing" as const, status_code: 404 }];
    const down = { ...SERVICE.service, health: "down" as const, urls };
    const last = pastIncident({
      id: "inc-9",
      title: "url failed on shop",
      closed_at: "2026-11-02T18:05:00Z",
    });
    stubBackend(json({ ...SERVICE, service: down }), [last, pastIncident()]);
    renderPage("demo/compose:shop");

    const note = await screen.findByRole("note", { name: "Still failing" });
    expect(within(note).getByRole("link", { name: "url failed on shop" })).toHaveAttribute(
      "href",
      "/incidents/inc-9",
    );
    expect(note).toHaveTextContent("Still down after its last incident ended");
    expect(note).toHaveTextContent("a wait that grows with each repeat, from an hour to a day");
  });

  it("says nothing of a last incident while one is open, the service is up or none ended", async () => {
    const down = { ...SERVICE.service, health: "down" as const };
    const cases: [ServiceDetail["service"], IncidentListItem[]][] = [
      [{ ...down, open_incident_id: "inc-2" }, [pastIncident()]],
      [SERVICE.service, [pastIncident()]],
      [down, [pastIncident({ status: "investigating" })]],
      [down, []],
    ];
    for (const [service, incidents] of cases) {
      stubBackend(json({ ...SERVICE, service }), incidents);
      const { unmount } = renderPage("demo/compose:shop");
      await waitFor(() => {
        expect(screen.queryByText("Loading the service")).toBeNull();
        expect(screen.queryByText("Loading the incidents")).toBeNull();
      });
      expect(screen.queryByRole("note", { name: "Still failing" })).toBeNull();
      unmount();
    }
  });

  it("says nothing of a wait when no check fails, as for a dead container", async () => {
    const down = { ...SERVICE.service, health: "down" as const };
    stubBackend(json({ ...SERVICE, service: down }), [pastIncident()]);
    renderPage("demo/compose:shop");

    const note = await screen.findByRole("note", { name: "Still failing" });
    expect(note).not.toHaveTextContent("wait");
  });

  it("says since when a healthy service has been healthy", async () => {
    const since = todayAt(9, 5);
    stubBackend(serviceWith({ health_since: since }));
    renderPage("demo/compose:shop");

    expect(await headline()).toBe("compose service on demo · 1 container · healthy since 9:05");
    const time = screen.getByText("9:05");
    expect(time.tagName).toBe("TIME");
    expect(time).toHaveAttribute("dateTime", since);
  });

  it("says nothing of since when the service is not healthy or the time is not known", async () => {
    stubBackend(serviceWith({ health_since: todayAt(9, 5) }, "degraded"));
    const { unmount } = renderPage("demo/compose:shop");
    expect(await headline()).toBe("compose service on demo · 1 container");
    unmount();

    stubBackend(serviceWith({ health_since: null }));
    renderPage("demo/compose:shop");
    expect(await headline()).toBe("compose service on demo · 1 container");
  });

  it("draws each URL's recent checks oldest first, with a summary for the strip", async () => {
    stubBackend(
      serviceWith({ recent_checks: [{ url: "http://localhost:8080/", checks: CHECKS }] }),
    );
    renderPage("demo/compose:shop");

    const strip = await screen.findByRole("list", {
      name: "Last 3 checks: 2 failed, oldest first, for http://localhost:8080/",
    });
    const ticks = within(strip).getAllByRole("img");
    expect(ticks.map((tick) => tick.getAttribute("aria-label"))).toEqual([
      `Passed at ${formatTime(CHECKS[0].checked_at, undefined, "en")}, 200, 12 ms`,
      `Failed at ${formatTime(CHECKS[1].checked_at, undefined, "en")}, 500, 30 ms`,
      `Failed at ${formatTime(CHECKS[2].checked_at, undefined, "en")}, no reply, 10000 ms`,
    ]);
    expect(ticks.map((tick) => tick.dataset.ok)).toEqual(["true", "false", "false"]);
    expect(ticks.map((tick) => tick.tabIndex)).toEqual([-1, -1, 0]); // one stop, on the newest
    expect(screen.getByText("Last 3 checks: 2 failed")).toBeInTheDocument();
  });

  it("says a tick's time, status code and latency on hover and focus, moving with the arrows", async () => {
    const user = userEvent.setup();
    stubBackend(
      serviceWith({ recent_checks: [{ url: "http://localhost:8080/", checks: CHECKS }] }),
    );
    renderPage("demo/compose:shop");
    const strip = await screen.findByRole("list", { name: /^Last 3 checks/ });
    const ticks = within(strip).getAllByRole("img");
    const first = `Passed at ${formatTime(CHECKS[0].checked_at, undefined, "en")}, 200, 12 ms`;
    const second = `Failed at ${formatTime(CHECKS[1].checked_at, undefined, "en")}, 500, 30 ms`;

    await user.hover(ticks[0]);
    expect(screen.getByText(first, { selector: "p" })).toBeInTheDocument();
    await user.unhover(strip);
    expect(screen.getByText("Last 3 checks: 2 failed")).toBeInTheDocument();

    ticks[2].focus();
    await user.keyboard("{ArrowLeft}");
    expect(ticks[1]).toHaveFocus();
    expect(screen.getByText(second, { selector: "p" })).toBeInTheDocument();
    await user.keyboard("{Home}");
    expect(ticks[0]).toHaveFocus();
    expect(ticks.map((tick) => tick.tabIndex)).toEqual([0, -1, -1]);
  });

  it("draws no strip for a URL with no checks yet", async () => {
    stubBackend(json(SERVICE));
    renderPage("demo/compose:shop");
    expect(await screen.findByRole("list", { name: "URLs" })).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: /^Last / })).not.toBeInTheDocument();
  });

  it("shows the human's right or wrong on each past incident that has any", async () => {
    stubBackend(json(SERVICE), [
      pastIncident(
        { id: "inc-1", title: "shop-web-1 died" },
        { diagnosis: "up", proposal: "down" },
      ),
      pastIncident(
        { id: "inc-2", title: "shop-db-1 is unhealthy" },
        { diagnosis: null, proposal: "up" },
      ),
      pastIncident({ id: "inc-3", title: "shop is slow" }),
    ]);
    renderPage("demo/compose:shop");
    const rows = within(await screen.findByRole("list", { name: "Incidents" })).getAllByRole(
      "link",
    );

    expect(within(rows[0]).getByText("Your feedback:")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Diagnosis right")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Plan wrong")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Plan right")).toBeInTheDocument();
    expect(rows[1]).not.toHaveTextContent("Diagnosis");
    expect(rows[2]).not.toHaveTextContent(/feedback|right|wrong/);
  });
});

describe("ServicePage settings", () => {
  it("shows the URLs in force with their checks, a new one as not checked yet", async () => {
    const view = settingsView();
    view.hosts[0].services["compose:shop"].urls.push({
      url: "https://shop.example.test/ready",
      expect_status: 204,
      body_contains: null,
    });
    settingsBackend({ reads: [view] });
    renderPage("m1/compose:shop");
    const urls = await region("Checked from outside");
    const rows = await within(urls).findAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Failing");
    expect(rows[1]).toHaveTextContent("https://shop.example.test/ready");
    expect(rows[1]).toHaveTextContent("Not checked yet");
  });

  it("saves the service's URLs as its host's overrides, keeping the others, and closes", async () => {
    const ready = {
      url: "https://shop.example.test/ready",
      expect_status: 204,
      body_contains: null,
    };
    const saved = settingsView({ version: "v2" });
    saved.hosts[0].services["compose:shop"].urls = [ready];
    saved.overrides.hosts = {
      m1: {
        services: { "compose:shop": { protected_containers: ["shop-cache-1"], urls: [ready] } },
      },
    };
    const fetchMock = settingsBackend({ puts: [json(saved)] });
    renderPage("m1/compose:shop");
    const urls = await region("Checked from outside");

    await userEvent.click(await within(urls).findByRole("button", { name: "Edit URLs" }));
    const url = within(urls).getByRole("textbox", { name: "URL 1" });
    await userEvent.clear(url);
    await userEvent.type(url, ready.url);
    const status = within(urls).getByRole("textbox", { name: "Expected status 1" });
    await userEvent.clear(status);
    await userEvent.type(status, "204");
    await userEvent.click(within(urls).getByRole("button", { name: "Save" }));

    expect(sentBody(fetchMock, "PUT")).toEqual({
      hosts: {
        m1: {
          services: {
            "compose:shop": { protected_containers: ["shop-cache-1"], urls: [ready] },
          },
        },
      },
    });
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
    expect(put?.[1]?.headers).toMatchObject({ "if-match": '"v1"' });
    expect(await within(urls).findByText(ready.url)).toBeInTheDocument();
    expect(within(urls).queryByRole("textbox")).toBeNull();
    expect(within(urls).getByText("Not checked yet")).toBeInTheDocument();
  });

  const SUGGESTED: UrlCheck[] = [
    { url: "https://shop.example.test/health/", expect_status: 200, body_contains: null },
    { url: "https://shop.example.com/health", expect_status: 200, body_contains: null },
    { url: "https://www.shop.example.com/health", expect_status: 200, body_contains: null },
  ];

  it("offers the checks Coolify suggests that the service lacks, and adds them in one click", async () => {
    const health = settingsView().hosts[0].services["compose:shop"].urls[0];
    const added = [health, SUGGESTED[1], SUGGESTED[2]];
    const saved = settingsView({ version: "v2" });
    saved.hosts[0].services["compose:shop"].urls = added;
    saved.overrides.hosts = {
      m1: { services: { "compose:shop": { protected_containers: ["shop-cache-1"], urls: added } } },
    };
    const fetchMock = settingsBackend({ puts: [json(saved)], suggested: SUGGESTED });
    renderPage("m1/compose:shop");
    const urls = await region("Checked from outside");

    const offer = await within(urls).findByRole("group", {
      name: "Checks Coolify suggests for shop",
    });
    expect(
      within(offer)
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual([SUGGESTED[1].url, SUGGESTED[2].url]);
    expect(offer).toHaveTextContent("I check none of them until you add them.");
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(false);

    await userEvent.click(within(offer).getByRole("button", { name: "Add these 2 checks" }));

    expect(sentBody(fetchMock, "PUT")).toEqual({
      hosts: {
        m1: {
          services: {
            "compose:shop": { protected_containers: ["shop-cache-1"], urls: added },
          },
        },
      },
    });
    await waitFor(() => expect(within(urls).queryByRole("group")).toBeNull());
    expect(within(urls).getAllByText("Not checked yet")).toHaveLength(2);
  });

  it("shows why adding the suggested checks was refused and keeps the offer", async () => {
    settingsBackend({
      puts: [
        invalid(
          ["hosts", "m1", "services", "compose:shop", "urls"],
          "List should have at most 20 items after validation, not 21",
        ),
      ],
      suggested: SUGGESTED.slice(1, 2),
    });
    renderPage("m1/compose:shop");
    const urls = await region("Checked from outside");
    const offer = await within(urls).findByRole("group");
    await userEvent.click(within(offer).getByRole("button", { name: "Add this check" }));

    expect(await within(offer).findByRole("alert")).toHaveTextContent(
      "List should have at most 20 items after validation, not 21",
    );
  });

  it("offers nothing when every suggested check is already in force", async () => {
    const fetchMock = settingsBackend({ suggested: SUGGESTED.slice(0, 1) });
    renderPage("m1/compose:shop");
    const urls = await region("Checked from outside");
    await within(urls).findByRole("button", { name: "Edit URLs" });
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) => String(input).endsWith("/suggested-checks")),
      ).toBe(true),
    );
    expect(within(urls).queryByRole("group")).toBeNull();
  });

  it("shows the API's reason when it refuses a URL, and keeps the draft open", async () => {
    settingsBackend({
      puts: [
        invalid(
          ["hosts", "m1", "services", "compose:shop", "urls"],
          "Value error, invalid url: must be http:// or https:// with a host and a valid port",
        ),
      ],
    });
    renderPage("m1/compose:shop");
    const urls = await region("Checked from outside");
    await userEvent.click(await within(urls).findByRole("button", { name: "Edit URLs" }));

    const url = within(urls).getByRole("textbox", { name: "URL 1" });
    await userEvent.clear(url);
    await userEvent.type(url, "ftp://shop");
    await userEvent.click(within(urls).getByRole("button", { name: "Save" }));

    expect(await within(urls).findByRole("alert")).toHaveTextContent(
      "URL: invalid url: must be http:// or https:// with a host and a valid port",
    );
    expect(url).toHaveValue("ftp://shop");
  });

  it("shows a URL row left empty as invalid and sends nothing", async () => {
    const fetchMock = settingsBackend();
    renderPage("m1/compose:shop");
    const urls = await region("Checked from outside");
    await userEvent.click(await within(urls).findByRole("button", { name: "Edit URLs" }));
    await userEvent.click(within(urls).getByRole("button", { name: "Add a URL" }));
    await userEvent.click(within(urls).getByRole("button", { name: "Save" }));

    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(false);
    expect(within(urls).getByRole("textbox", { name: "URL 2" })).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(within(urls).getByRole("alert")).toHaveTextContent(
      "URL 2: enter an address, or remove the row",
    );
  });

  it("Cancel closes the editor and sends nothing", async () => {
    const fetchMock = settingsBackend();
    renderPage("m1/compose:shop");
    const urls = await region("Checked from outside");
    await userEvent.click(await within(urls).findByRole("button", { name: "Edit URLs" }));
    await userEvent.type(within(urls).getByRole("textbox", { name: "URL 1" }), "x");
    await userEvent.click(within(urls).getByRole("button", { name: "Cancel" }));

    expect(within(urls).queryByRole("textbox")).toBeNull();
    expect(within(urls).getByText("https://shop.example.test/health")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(false);
  });

  it("edits the service's own rules, shows its host's, and brings the hosts file's back", async () => {
    const changed = settingsView({ version: "v2" });
    changed.hosts[0].services["compose:shop"].rules = ["Never restart it."];
    changed.overrides.hosts = {
      m1: {
        services: {
          "compose:shop": { protected_containers: ["shop-cache-1"], rules: ["Never restart it."] },
        },
      },
    };
    const fetchMock = settingsBackend({
      puts: [json(changed), json(settingsView({ version: "v3" }))],
    });
    renderPage("m1/compose:shop");
    const rules = await region("Rules I follow");
    expect(await within(rules).findByRole("list", { name: "Rules: Its host" })).toHaveTextContent(
      "Ask before touching anything at night.",
    );

    await userEvent.click(within(rules).getByRole("button", { name: "Edit rules" }));
    expect(within(rules).getByText("From the hosts file")).toBeInTheDocument();
    const rule = within(rules).getByRole("textbox", { name: "Rules for shop: entry 1" });
    await userEvent.clear(rule);
    await userEvent.type(rule, "Never restart it.");
    await userEvent.click(within(rules).getByRole("button", { name: "Save" }));
    expect(sentBody(fetchMock, "PUT")).toEqual(changed.overrides);
    expect(
      await within(rules).findByRole("list", { name: "Rules: This service" }),
    ).toHaveTextContent("Never restart it.");

    await userEvent.click(within(rules).getByRole("button", { name: "Edit rules" }));
    await userEvent.click(within(rules).getByRole("button", { name: "Use the hosts file's" }));
    expect(sentBody(fetchMock, "PUT", 1)).toEqual({
      hosts: { m1: { services: { "compose:shop": { protected_containers: ["shop-cache-1"] } } } },
    });
  });

  it("edits the jobs expected to exit, saved as the service's list", async () => {
    const saved = settingsView({ version: "v2" });
    saved.hosts[0].services["compose:shop"].exits = ["fetch"];
    const fetchMock = settingsBackend({ puts: [json(saved)] });
    renderPage("m1/compose:shop");
    const containers = await region("Containers");
    expect(await within(containers).findByText("Expected to exit")).toBeInTheDocument();

    await userEvent.click(within(containers).getByRole("button", { name: "Edit jobs" }));
    await userEvent.type(
      within(containers).getByPlaceholderText(/Container or Compose service name/),
      "fetch{Enter}",
    );
    await userEvent.click(within(containers).getByRole("button", { name: "Save" }));
    expect(sentBody(fetchMock, "PUT").hosts.m1.services["compose:shop"].exits).toEqual(["fetch"]);
    expect(
      await within(containers).findByRole("list", { name: "Expected to exit: shop" }),
    ).toHaveTextContent("fetch");
  });

  it("keeps the hosts file's protected containers locked and removes only the dashboard's", async () => {
    const saved = settingsView({ version: "v2" });
    saved.overrides.hosts = { m1: { services: {} } };
    saved.hosts[0].services["compose:shop"].protected_containers = ["shop-db-1"];
    const fetchMock = settingsBackend({ puts: [json(saved)] });
    renderPage("m1/compose:shop");
    const containers = await region("Containers");

    const fixed = await within(containers).findByRole("list", {
      name: "Protected by the hosts file: shop",
    });
    expect(within(fixed).getByText("shop-db-1")).toBeInTheDocument();
    expect(
      within(containers).getByRole("list", { name: "Protected here: shop" }),
    ).toHaveTextContent("shop-cache-1");

    await userEvent.click(
      within(containers).getByRole("button", { name: "Edit protected containers" }),
    );
    expect(within(containers).queryByRole("button", { name: "Remove shop-db-1" })).toBeNull();
    await userEvent.click(within(containers).getByRole("button", { name: "Remove shop-cache-1" }));
    await userEvent.click(within(containers).getByRole("button", { name: "Save" }));

    expect(sentBody(fetchMock, "PUT")).toEqual({ hosts: { m1: { services: {} } } });
    await waitFor(() =>
      expect(within(containers).queryByRole("list", { name: "Protected here: shop" })).toBeNull(),
    );
    expect(within(fixed).getByText("shop-db-1")).toBeInTheDocument();
  });

  it("after a 409 reads the settings again, says the save was not applied and never retries it", async () => {
    // Elsewhere, shop-web-1 was protected after this page loaded; the stale save would drop it.
    const now = settingsView({ version: "v2" });
    now.overrides.hosts = {
      m1: {
        services: { "compose:shop": { protected_containers: ["shop-cache-1", "shop-web-1"] } },
      },
    };
    const fetchMock = settingsBackend({
      reads: [settingsView(), now],
      puts: [json({ detail: "the settings changed", version: "v2" }, 409)],
    });
    renderPage("m1/compose:shop");
    const containers = await region("Containers");
    await userEvent.click(
      await within(containers).findByRole("button", { name: "Edit protected containers" }),
    );
    await userEvent.click(within(containers).getByRole("button", { name: "Remove shop-cache-1" }));
    await userEvent.click(within(containers).getByRole("button", { name: "Save" }));

    expect((await screen.findAllByRole("alert"))[0]).toHaveTextContent(
      MESSAGES.en.settings.changedElsewhere,
    );
    expect(await within(containers).findByDisplayValue("shop-web-1")).toBeInTheDocument();
    expect(within(containers).getByDisplayValue("shop-cache-1")).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT")).toHaveLength(1);
    expect(fetchMock.mock.calls.filter(([path]) => path === "/api/settings")).toHaveLength(3);
  });

  it("says the settings could not be read again after a 409, rather than that they were", async () => {
    settingsBackend({
      reads: [settingsView(), json({ detail: "Bad Gateway" }, 502)],
      puts: [json({ detail: "the settings changed", version: "v2" }, 409)],
    });
    renderPage("m1/compose:shop");
    const rules = await region("Rules I follow");
    await userEvent.click(await within(rules).findByRole("button", { name: "Edit rules" }));
    await userEvent.type(within(rules).getByPlaceholderText(/Add a rule/), "r{Enter}");
    await userEvent.click(within(rules).getByRole("button", { name: "Save" }));

    const alert = (await screen.findAllByRole("alert"))[0];
    expect(alert).toHaveTextContent("Reading them again failed (Bad Gateway)");
    expect(alert).not.toHaveTextContent("have been read again");
  });

  it("lets one save be in flight at a time, so the next is built from its result", async () => {
    let answer: (response: Response) => void = () => undefined;
    const saved = settingsView({ version: "v2" });
    saved.overrides.hosts = {
      m1: {
        services: { "compose:shop": { protected_containers: ["shop-cache-1", "shop-web-1"] } },
      },
    };
    saved.hosts[0].services["compose:shop"].protected_containers = ["shop-cache-1", "shop-web-1"];
    const fetchMock = settingsBackend({
      puts: [new Promise<Response>((resolve) => (answer = resolve)), json(saved)],
    });
    renderPage("m1/compose:shop");
    const containers = await region("Containers");
    const rules = await region("Rules I follow");
    await userEvent.click(
      await within(containers).findByRole("button", { name: "Edit protected containers" }),
    );
    await userEvent.click(within(rules).getByRole("button", { name: "Edit rules" }));

    await userEvent.type(
      within(containers).getByPlaceholderText("Container name, such as shop-db-1"),
      "shop-web-1{Enter}",
    );
    await userEvent.click(within(containers).getByRole("button", { name: "Save" }));
    await userEvent.type(
      within(rules).getByRole("textbox", { name: "Rules for shop: entry 1" }),
      "!",
    );
    expect(within(rules).getByRole("button", { name: "Save" })).toBeDisabled();
    expect(within(containers).getByRole("button", { name: "Save" })).toBeDisabled();

    answer(json(saved));
    await waitFor(() => expect(within(rules).getByRole("button", { name: "Save" })).toBeEnabled());
    await userEvent.click(within(rules).getByRole("button", { name: "Save" }));
    expect(sentBody(fetchMock, "PUT", 1)).toEqual({
      hosts: {
        m1: {
          services: {
            "compose:shop": {
              protected_containers: ["shop-cache-1", "shop-web-1"],
              rules: ["The db is shared with billing.!"],
            },
          },
        },
      },
    });
    expect(
      fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT")[1][1]?.headers,
    ).toMatchObject({ "if-match": '"v2"' });
  });

  it("edits a service with no settings yet", async () => {
    const view = settingsView({ overrides: {} });
    view.hosts[0].services = {};
    settingsBackend({ reads: [view] });
    renderPage("m1/compose:shop");
    const urls = await region("Checked from outside");
    expect(await within(urls).findByText("No URLs are checked.")).toBeInTheDocument();
    expect(within(urls).getByRole("button", { name: "Edit URLs" })).toBeInTheDocument();
    expect(await region("Containers")).toHaveTextContent("None.");
  });

  it("finds and saves settings the hosts file keeps under the service's name", async () => {
    const view = settingsView({ overrides: {} });
    view.hosts[0].services = { shop: view.hosts[0].services["compose:shop"] };
    const fetchMock = settingsBackend({ reads: [view], puts: [json(view)] });
    renderPage("m1/compose:shop");
    const rules = await region("Rules I follow");
    await userEvent.click(await within(rules).findByRole("button", { name: "Edit rules" }));
    await userEvent.type(
      within(rules).getByRole("textbox", { name: "Rules for shop: entry 1" }),
      "!",
    );
    await userEvent.click(within(rules).getByRole("button", { name: "Save" }));
    expect(sentBody(fetchMock, "PUT")).toEqual({
      hosts: { m1: { services: { shop: { rules: ["The db is shared with billing.!"] } } } },
    });
  });

  it("edits the settings of a service Bleep does not see now", async () => {
    const view = settingsView({ overrides: {} });
    view.hosts[0].services["compose:old"] = view.hosts[0].services["compose:shop"];
    const fetchMock = settingsBackend({
      service: json({ detail: "no such service" }, 404),
      reads: [view],
      puts: [json(view)],
    });
    renderPage("m1/compose:old");
    expect(await screen.findByText(/does not see this service/)).toBeInTheDocument();
    expect(await region("Checked from outside")).toHaveTextContent(
      "https://shop.example.test/health",
    );
    const rules = await region("Rules I follow");
    await userEvent.click(within(rules).getByRole("button", { name: "Edit rules" }));
    await userEvent.clear(within(rules).getByRole("textbox", { name: "Rules for old: entry 1" }));
    await userEvent.click(within(rules).getByRole("button", { name: "Save" }));
    expect(sentBody(fetchMock, "PUT")).toEqual({
      hosts: { m1: { services: { "compose:old": { rules: [] } } } },
    });
  });

  it("finds a gone service's settings the hosts file keeps under its name", async () => {
    const view = settingsView({ overrides: {} });
    view.hosts[0].services = { shop: view.hosts[0].services["compose:shop"] };
    settingsBackend({ service: json({ detail: "no such service" }, 404), reads: [view] });
    renderPage("m1/compose:shop");
    expect(await region("Rules I follow")).toHaveTextContent("The db is shared with billing.");
  });

  it("shows no settings for a gone service that has none", async () => {
    const fetchMock = settingsBackend({ service: json({ detail: "no such service" }, 404) });
    renderPage("m1/compose:gone");
    expect(await screen.findByText(/does not see this service/)).toBeInTheDocument();
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([path]) => path === "/api/settings")).toBe(true),
    );
    expect(await screen.findByText(/No incidents yet/)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Checked from outside" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Rules I follow" })).toBeNull();
  });

  it("falls back to the service's read when the settings cannot be loaded", async () => {
    settingsBackend({ reads: [json({ detail: "the store is down" }, 503)] });
    renderPage("m1/compose:shop");
    expect(await screen.findByText(/Could not load the service's settings/)).toHaveTextContent(
      "the store is down",
    );
    const rules = await region("Rules I follow");
    expect(within(rules).getByRole("list", { name: "Rules: This service" })).toHaveTextContent(
      "The shop db is shared with billing.",
    );
    expect(within(rules).queryByRole("button", { name: "Edit rules" })).toBeNull();
  });
});

describe("ServicePage in German", () => {
  beforeEach(() => act(() => setLanguage("de")));

  it("speaks German on the page, the checks, the rules and the past incidents", async () => {
    const fetchMock = stubBackend(
      serviceWith({
        health_since: todayAt(9, 5),
        recent_checks: [{ url: "http://localhost:8080/", checks: CHECKS }],
      }),
      [
        pastIncident(
          { id: "inc-1", title: "shop-web-1 died", opened_at: "2026-11-02T19:00:00Z" },
          { diagnosis: "up", proposal: "down" },
        ),
      ],
    );
    renderPage("demo/compose:shop");

    expect((await screen.findByText(/-Dienst auf/, { selector: "p" })).textContent).toBe(
      "compose-Dienst auf demo · 1 Container · gesund seit 9:05",
    );
    const crumbs = screen.getByRole("navigation", { name: "Brotkrümelnavigation" });
    expect(within(crumbs).getByRole("link", { name: "Start" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("region", { name: "Was ich über shop weiß" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Von außen geprüft" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Container" })).toBeInTheDocument();
    const rules = screen.getByRole("region", { name: "Regeln, an die ich mich halte" });
    expect(within(rules).getByRole("list", { name: "Regeln: Dieser Dienst" })).toBeInTheDocument();
    expect(within(rules).getByRole("list", { name: "Regeln: Sein Host" })).toBeInTheDocument();
    expect(within(rules).getByText("Ich ändere nie etwas ohne dein Ja.")).toBeInTheDocument();
    expect(await screen.findByText(/Noch keine Fakten/)).toBeInTheDocument();

    const strip = screen.getByRole("list", {
      name: "Letzte 3 Prüfungen: 2 fehlgeschlagen, älteste zuerst, für http://localhost:8080/",
    });
    expect(within(strip).getAllByRole("img")[0]).toHaveAttribute(
      "aria-label",
      `Bestanden um ${formatTime(CHECKS[0].checked_at, undefined, "de")}, 200, 12 ms`,
    );
    const past = await screen.findByRole("list", { name: "Vorfälle" });
    expect(within(past).getByText("Diagnose richtig")).toBeInTheDocument();
    expect(within(past).getByText("Plan falsch")).toBeInTheDocument();
    expect(within(past).getByText("02.11.2026, 20:00")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalled();
  });

  it("says the service is still down after its last incident, and what a quiet one means", async () => {
    const urls = [{ ...SERVICE.service.urls[0], status: "failing" as const, status_code: 404 }];
    const down = { ...SERVICE.service, health: "down" as const, urls };
    stubBackend(json({ ...SERVICE, service: down }), [
      pastIncident({ id: "inc-9", title: "url failed on shop", closed_at: "2026-11-02T18:05:00Z" }),
    ]);
    const { unmount } = renderPage("demo/compose:shop");
    const note = await screen.findByRole("note", { name: "Besteht weiterhin" });
    expect(note).toHaveTextContent("Weiterhin ausgefallen; sein letzter Vorfall endete");
    expect(note).toHaveTextContent("sobald eine Prüfung bestanden wurde");
    unmount();

    const since = todayAt(14, 2);
    const hostQuiet = {
      scope: "host",
      mode: "snooze",
      until: new Date(2030, 0, 1, 18, 0).toISOString(),
      since,
    };
    stubBackend(json({ ...SERVICE, service: { ...SERVICE.service, quiet: hostQuiet } }));
    renderPage("demo/compose:shop");
    expect((await screen.findByRole("note")).textContent).toMatch(
      /^Sein Host ist bis .*18:00 stumm, deshalb öffnet Bleep keine Vorfälle für ihn\.$/,
    );
  });

  it("lets a long German word in a heading wrap and hyphenate", async () => {
    const name = "Donaudampfschifffahrtsgesellschaftskapitän";
    stubBackend(json({ ...SERVICE, service: { ...SERVICE.service, name } }));
    renderPage("demo/compose:shop");
    expect(await screen.findByRole("heading", { level: 1, name })).toHaveClass(
      "wrap-anywhere",
      "hyphens-auto",
    );
  });

  it("says a service is gone and what failed to load, in German", async () => {
    stubBackend(json({ detail: "kaputt" }, 500));
    renderPage("demo/compose:shop");
    expect(await screen.findByText(/Der Dienst konnte nicht geladen werden/)).toBeInTheDocument();
  });
});
