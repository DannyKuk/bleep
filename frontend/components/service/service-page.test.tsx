import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { incident } from "@/components/incident/fixtures";
import { ServicePage } from "@/components/service/service-page";
import type { ServiceDetail } from "@/lib/api-types";

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
  const own = { scope: "service", mode: "leave_alone", until: null, since: "2026-11-04T14:02:00" };

  function quietService(quiet: unknown) {
    return json({ ...SERVICE, service: { ...SERVICE.service, quiet } });
  }

  it("offers Quiet and, while left alone, a banner with Watch again", async () => {
    stubBackend(quietService(null));
    const { unmount } = render(<ServicePage id="demo/compose:shop" />);
    expect(await screen.findByRole("button", { name: "Quiet shop" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Watch again" })).toBeNull();
    unmount();

    stubBackend(quietService(own));
    render(<ServicePage id="demo/compose:shop" />);
    const banner = await screen.findByRole("note");
    expect(banner).toHaveTextContent("Left alone since 14:02. Bleep opens no incidents for it.");
    expect(within(banner).getByRole("button", { name: "Watch again" })).toBeInTheDocument();
  });

  it("Watch again on the banner DELETEs and reloads", async () => {
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
    await userEvent.click(await screen.findByRole("button", { name: "Watch again" }));
    await waitFor(() => expect(screen.queryByRole("note")).toBeNull());
    const deletes = fetchMock.mock.calls.filter(([, init]) => init?.method === "DELETE");
    expect(String(deletes[0][0])).toBe(QUIET_PATH);
  });

  it("shows a snooze's end in the banner", async () => {
    const until = new Date(2030, 0, 1, 18, 0).toISOString();
    stubBackend(quietService({ ...own, mode: "snooze", until }));
    render(<ServicePage id="demo/compose:shop" />);
    expect((await screen.findByRole("note")).textContent).toMatch(
      /^Snoozed until .*18:00\. Bleep opens no incidents for it\./,
    );
  });

  it("a service quiet through its host shows the host banner without Watch again", async () => {
    stubBackend(quietService({ ...own, scope: "host" }));
    render(<ServicePage id="demo/compose:shop" />);
    const banner = await screen.findByRole("note");
    expect(banner).toHaveTextContent(/^Its host is left alone/);
    expect(within(banner).queryByRole("button", { name: "Watch again" })).toBeNull();
  });
});
