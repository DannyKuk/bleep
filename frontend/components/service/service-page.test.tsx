import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { incident } from "@/components/incident/fixtures";
import { OverallProvider } from "@/components/overall-state";
import { ServicePage } from "@/components/service/service-page";
import { invalid, sentBody, settingsView } from "@/components/settings/fixtures";
import type { IncidentListItem, ServiceDetail, UrlCheckPoint } from "@/lib/api-types";
import { formatTime } from "@/lib/format";
import { SETTINGS_CHANGED } from "@/lib/settings-api";

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
  health_since: null,
  recent_checks: [{ url: "http://localhost:8080/", checks: [] }],
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** The header's overall poll (`OverallProvider`): the newest incidents and the hosts. */
function answerOverall(input: RequestInfo | URL): Response | undefined {
  const url = String(input);
  if (url === "/api/hosts") return json([]);
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
  return { ...incident({ status: "closed" }), votes, ...overrides };
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
}: {
  service?: Response;
  reads?: (ReturnType<typeof settingsView> | Response)[];
  puts?: (Response | Promise<Response>)[];
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

describe("ServicePage history", () => {
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
      `Passed at ${formatTime(CHECKS[0].checked_at)}, 200, 12 ms`,
      `Failed at ${formatTime(CHECKS[1].checked_at)}, 500, 30 ms`,
      `Failed at ${formatTime(CHECKS[2].checked_at)}, no reply, 10000 ms`,
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
    const first = `Passed at ${formatTime(CHECKS[0].checked_at)}, 200, 12 ms`;
    const second = `Failed at ${formatTime(CHECKS[1].checked_at)}, 500, 30 ms`;

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

    expect((await screen.findAllByRole("alert"))[0]).toHaveTextContent(SETTINGS_CHANGED);
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
