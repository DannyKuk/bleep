import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { json } from "@/components/dev/fixtures";
import { approval, diagnosis, incident, proposal } from "@/components/incident/fixtures";
import { OverallProvider } from "@/components/overall-state";
import { host, service } from "@/components/overview/fixtures";
import { Overview, OverviewView } from "@/components/overview/overview";
import type { IncidentDetail } from "@/lib/api-types";

const NOW = Date.parse("2026-11-02T19:00:00Z");

function detail(overrides: Partial<IncidentDetail> = {}): IncidentDetail {
  return {
    incident: incident(),
    diagnosis: diagnosis(),
    proposal: proposal(),
    approval: approval(),
    outcome: null,
    report: null,
    feedback: [],
    ...overrides,
  };
}

const headline = () => screen.getByRole("heading", { level: 1 });
const hero = () => headline().closest("section")!;
const mood = (container: HTMLElement) =>
  container.querySelector("svg[data-mood]")?.getAttribute("data-mood");

describe("OverviewView hero", () => {
  it("says what failed and that Bleep has a fix, with the plan in one line", () => {
    const { container } = render(
      <OverviewView
        hosts={[host({ health: "down" })]}
        services={[service({ health: "down", open_incident_id: "inc-1" })]}
        incidents={[incident()]}
        details={{
          "inc-1": detail({
            approval: approval({
              proposal: proposal({
                steps: [
                  { action: "docker.start", host_id: "demo", target: "shop-db", params: {} },
                  { action: "docker.restart", host_id: "demo", target: "shop-web-1", params: {} },
                ],
              }),
            }),
          }),
        }}
        now={NOW}
      />,
    );
    expect(headline()).toHaveTextContent("shop is down. I know why, and I have a fix.");
    expect(screen.getByText(/I want to/)).toHaveTextContent(
      "I want to start shop-db, then restart shop-web-1. I will not touch anything until you say yes.",
    );
    expect(screen.getByRole("link", { name: /Show me the plan/ })).toHaveAttribute(
      "href",
      "/incidents/inc-1",
    );
    expect(screen.getByText("Needs your yes")).toBeInTheDocument();
    expect(mood(container)).toBe("needs_you");
  });

  it("reads a plan refused before the refused flag existed as refused, not as a fix", () => {
    render(
      <OverviewView
        hosts={[host({ health: "down" })]}
        services={[service({ health: "down" })]}
        incidents={[incident()]}
        details={{
          "inc-1": detail({
            approval: approval({
              proposal: proposal({
                kind: "no_action",
                steps: [],
                reason: "Bleep's own checks refused the proposed plan twice, so nothing will run.",
              }),
            }),
          }),
        }}
        now={NOW}
      />,
    );
    expect(headline()).toHaveTextContent("My own checks stopped my plan, so I need you.");
    expect(headline()).not.toHaveTextContent("I have a fix");
  });

  it("shows the incident's title until the waiting incident's detail is read", () => {
    render(
      <OverviewView
        hosts={[host({ health: "down" })]}
        services={[service({ health: "down" })]}
        incidents={[incident({ title: "shop-web-1 died" })]}
        now={NOW}
      />,
    );
    expect(headline()).toHaveTextContent("shop is down. I need you.");
    expect(screen.getByText("shop-web-1 died")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Show me the plan/ })).toBeInTheDocument();
  });

  it("says when Bleep is not sure, and wears that face", () => {
    const { container } = render(
      <OverviewView
        hosts={[host({ health: "down" })]}
        services={[service({ health: "down" })]}
        incidents={[incident()]}
        details={{ "inc-1": detail({ diagnosis: diagnosis({ unsure: true }) }) }}
        now={NOW}
      />,
    );
    expect(headline()).toHaveTextContent("shop is down. I am not sure why, but I have a plan.");
    expect(mood(container)).toBe("not_sure");
  });

  it("never promises a fix for a plan with nothing to run", () => {
    render(
      <OverviewView
        hosts={[host({ health: "down" })]}
        services={[service({ health: "down" })]}
        incidents={[incident()]}
        details={{
          "inc-1": detail({
            approval: approval({ proposal: proposal({ kind: "no_action", steps: [] }) }),
          }),
        }}
        now={NOW}
      />,
    );
    expect(headline()).toHaveTextContent("shop is down. I know why, but I have no fix to run.");
    expect(screen.queryByText(/I want to/)).toBeNull();
  });

  it("counts the other incidents that need the human", () => {
    render(
      <OverviewView
        hosts={[host()]}
        services={[service()]}
        incidents={[incident({ id: "a" }), incident({ id: "b" })]}
        now={NOW}
      />,
    );
    expect(screen.getByRole("link", { name: "1 more incident needs you" })).toHaveAttribute(
      "href",
      "/incidents",
    );
  });

  it("makes an unreachable host the headline", () => {
    const { container } = render(
      <OverviewView
        hosts={[
          host({
            id: "main",
            name: "coolify-main",
            kind: "ssh",
            reachable: false,
            health: "down",
            error: "ssh: connection timed out",
          }),
          host(),
        ]}
        services={[
          service({ id: "main/a", host_id: "main", name: "molecula", health: "unknown" }),
          service({ id: "main/b", host_id: "main", name: "blog", health: "unknown" }),
          service(),
        ]}
        incidents={[]}
        now={NOW}
      />,
    );
    expect(headline()).toHaveTextContent("I cannot reach coolify-main.");
    expect(screen.getByText(/so I cannot see molecula and blog\./)).toBeInTheDocument();
    expect(within(hero()).getByText("ssh: connection timed out")).toBeInTheDocument();
    expect(mood(container)).toBe("working");
  });

  it("says nothing needs the human when all is well", () => {
    const { container } = render(
      <OverviewView
        hosts={[host(), host({ id: "spare", name: "Spare M1" })]}
        services={[service()]}
        incidents={[incident({ status: "resolved" })]}
        now={NOW}
      />,
    );
    expect(headline()).toHaveTextContent("Nothing needs you right now.");
    expect(screen.getByText("I am watching 2 hosts and 1 service.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Show me/ })).toBeNull();
    expect(mood(container)).toBe("all_good");
  });
});

describe("OverviewView hosts", () => {
  it("says how far a problem reaches above the tiles", () => {
    render(
      <OverviewView
        hosts={[
          host({ id: "demo", name: "Demo stack", health: "down" }),
          host({ id: "main", name: "coolify-main" }),
          host({ id: "sites", name: "coolify-sites" }),
        ]}
        services={[
          service({ id: "demo/a", name: "shop", health: "down" }),
          service({ id: "demo/b", name: "worker" }),
        ]}
        incidents={[]}
        now={NOW}
      />,
    );
    expect(
      screen.getByText("Only Demo stack is hurting: 1 of its 2 services. The other two are fine."),
    ).toBeInTheDocument();
  });

  it("puts each host's services in its tile, linked to their pages, and tints a host in trouble", () => {
    render(
      <OverviewView
        hosts={[
          host({ id: "demo", name: "Demo stack", health: "down" }),
          host({ id: "spare", name: "Spare M1", kind: "ssh" }),
        ]}
        services={[
          service({ id: "demo/compose:shop", host_id: "demo", name: "shop", health: "down" }),
          service({ id: "spare/compose:blog", host_id: "spare", name: "blog" }),
        ]}
        incidents={[]}
        now={NOW}
      />,
    );
    const demo = screen.getByRole("region", { name: "Demo stack" });
    const spare = screen.getByRole("region", { name: "Spare M1" });
    expect(within(demo).getByRole("link", { name: "shop" })).toHaveAttribute(
      "href",
      "/services/demo%2Fcompose%3Ashop",
    );
    expect(within(demo).queryByText("blog")).toBeNull();
    expect(within(spare).getByText("ssh")).toBeInTheDocument();
    expect(demo).toHaveAttribute("data-hurting", "true");
    expect(spare).toHaveAttribute("data-hurting", "false");
  });

  it("links a service's open incident from its row", () => {
    render(
      <OverviewView
        hosts={[host({ health: "down" })]}
        services={[service({ health: "down", open_incident_id: "inc_42" })]}
        incidents={[]}
        now={NOW}
      />,
    );
    const tile = screen.getByRole("region", { name: "Demo stack" });
    expect(within(tile).getByRole("link", { name: /open incident/i })).toHaveAttribute(
      "href",
      "/incidents/inc_42",
    );
  });

  it("shows a host's basics in one line", () => {
    render(
      <OverviewView
        hosts={[
          host({
            basics: {
              os: "linux",
              cpus: 4,
              mem_total: 8_000,
              mem_available: 2_000,
              load1: 0.34,
              load15: 0.2,
              disks: [
                { mount: "/", used_percent: 41 },
                { mount: "/data", used_percent: 58 },
              ],
            },
          }),
        ]}
        services={[service()]}
        incidents={[]}
        now={NOW}
      />,
    );
    expect(screen.getByText("load 0.3 · mem 75% · disk 58%")).toBeInTheDocument();
  });

  it("shows the empty state with a docs link", () => {
    render(<OverviewView hosts={[]} services={[]} incidents={[]} now={NOW} />);
    expect(headline()).toHaveTextContent("I have no hosts to watch yet.");
    expect(screen.getByText(/config\/hosts\.yaml/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /setup/i })).toHaveAttribute(
      "href",
      expect.stringContaining("github.com/DannyKuk/bleep"),
    );
  });
});

describe("Overview", () => {
  it("reads the detail only of incidents that wait for the human", async () => {
    const waiting = incident({ id: "wait" });
    const fetchMock = vi.fn<typeof fetch>(async (input) => {
      const path = String(input);
      if (path.startsWith("/api/incidents?")) {
        return json([waiting, incident({ id: "busy", status: "investigating" })]);
      }
      if (path === "/api/hosts") return json([host({ health: "down" })]);
      if (path === "/api/services") return json([service({ health: "down" })]);
      if (path === "/api/incidents/wait") return json(detail({ incident: waiting }));
      return json({ detail: "not found" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <OverallProvider>
        <Overview />
      </OverallProvider>,
    );
    expect(await screen.findByText(/I want to/)).toHaveTextContent("I want to start shop-web-1.");
    const paths = fetchMock.mock.calls.map(([input]) => String(input));
    expect(paths).toContain("/api/incidents/wait");
    expect(paths).not.toContain("/api/incidents/busy");
    expect(paths.filter((path) => path === "/api/hosts")).toHaveLength(1);
  });

  it("shows the title when the detail cannot be read", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async (input) => {
        const path = String(input);
        if (path.startsWith("/api/incidents?")) return json([incident()]);
        if (path === "/api/hosts") return json([host()]);
        if (path === "/api/services") return json([service()]);
        return json({ detail: "boom" }, 500);
      }),
    );
    render(
      <OverallProvider>
        <Overview />
      </OverallProvider>,
    );
    expect(await screen.findByText("shop-web-1 died")).toBeInTheDocument();
    expect(headline()).toHaveTextContent("I need you.");
  });
});
