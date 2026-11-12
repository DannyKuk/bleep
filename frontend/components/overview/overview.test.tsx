import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { json } from "@/components/dev/fixtures";
import { stubBackend } from "@/components/settings/fixtures";
import { approval, diagnosis, incident, proposal } from "@/components/incident/fixtures";
import { OverallProvider } from "@/components/overall-state";
import { host, service, watch } from "@/components/overview/fixtures";
import { Overview, OverviewView } from "@/components/overview/overview";
import type { IncidentDetail, ServiceView, UnwatchedReason, UnwatchedView } from "@/lib/api-types";
import { setLanguage } from "@/lib/i18n";

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
    expectation: null,
    repeats: 1,
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

  it("puts the short name in front of the title it follows along (#397)", () => {
    render(
      <OverviewView
        hosts={[host({ health: "down" })]}
        services={[service({ health: "down", open_incident_id: "inc-1" })]}
        incidents={[incident({ status: "investigating", short_name: "shop #14" })]}
        now={NOW}
      />,
    );
    expect(headline()).toHaveTextContent("shop is down. I am on it.");
    expect(hero()).toHaveTextContent("shop #14 · shop-web-1 died");
    expect(within(hero()).getByText("shop #14")).toHaveClass("font-mono", "text-ink-2");
    expect(screen.getByRole("link", { name: /Follow along/ })).toHaveAttribute(
      "href",
      "/incidents/inc-1",
    );
  });

  it("shows the title alone it follows along when the incident has no short name", () => {
    render(
      <OverviewView
        hosts={[host({ health: "down" })]}
        services={[service({ health: "down", open_incident_id: "inc-1" })]}
        incidents={[incident({ status: "investigating" })]}
        now={NOW}
      />,
    );
    expect(screen.getByText("shop-web-1 died")).toHaveTextContent(/^shop-web-1 died$/);
    expect(hero()).not.toHaveTextContent("·");
    expect(screen.getByRole("link", { name: /Follow along/ })).toBeInTheDocument();
  });

  it("puts the short name in front of the title of a waiting or failed incident too", () => {
    for (const status of ["awaiting_approval", "failed"] as const) {
      const { unmount } = render(
        <OverviewView
          hosts={[host({ health: "down" })]}
          services={[service({ health: "down" })]}
          incidents={[incident({ status, short_name: "shop #14" })]}
          now={NOW}
        />,
      );
      expect(hero(), status).toHaveTextContent("shop #14 · shop-web-1 died");
      unmount();
    }
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

  it("asks how for a plan of no action whose fix Bleep could run, without a plan to approve", () => {
    const couldRun = proposal({ kind: "no_action", steps: [], fix_runnable: true });
    render(
      <OverviewView
        hosts={[host({ health: "down" })]}
        services={[service({ health: "down" })]}
        incidents={[incident()]}
        details={{ "inc-1": detail({ approval: approval({ proposal: couldRun }) }) }}
        now={NOW}
      />,
    );
    expect(headline()).toHaveTextContent("shop is down. I could run the fix once you tell me how.");
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
    expect(screen.getByText(/so I cannot see molecula and blog\.$/)).toBeInTheDocument();
    expect(within(hero()).getByText("ssh: connection timed out")).toBeInTheDocument();
    expect(mood(container)).toBe("working");
  });

  it("watches an unreachable host while the watcher waits out its window (#358)", () => {
    const window = watch({
      kind: "unreachable",
      url: null,
      failures: null,
      failures_to_open: null,
      opens_after: "2026-11-02T19:04:00Z",
    });
    const { container } = render(
      <OverviewView
        hosts={[host({ id: "main", name: "coolify-main", reachable: false, watching: window })]}
        services={[]}
        incidents={[]}
        now={NOW}
      />,
    );
    expect(headline()).toHaveTextContent("I cannot reach coolify-main.");
    expect(hero()).toHaveTextContent(
      /If it does not answer by \d\d:\d\d[^,]*, I will look into it\./,
    );
    expect(mood(container)).toBe("watching");
  });

  it("follows the incident about an unreachable host instead of promising to look", () => {
    const { container } = render(
      <OverviewView
        hosts={[host({ id: "main", name: "coolify-main", reachable: false, health: "down" })]}
        services={[]}
        incidents={[
          incident({
            id: "host-inc",
            status: "investigating",
            subject: { kind: "host", host_id: "main", service_id: null },
          }),
        ]}
        now={NOW}
      />,
    );
    expect(headline()).toHaveTextContent("I cannot reach coolify-main.");
    expect(hero()).not.toHaveTextContent("I will look into it");
    expect(screen.getByRole("link", { name: /Follow along/ })).toHaveAttribute(
      "href",
      "/incidents/host-inc",
    );
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

describe("OverviewView hero while Bleep watches (#358)", () => {
  const failingUrl = {
    url: "http://testapp.local/",
    status: "failing",
    last_checked: null,
    status_code: 503,
    latency_ms: 40,
  } as const;
  const deadContainer = {
    ...service().containers[0],
    state: "exited",
    exit_code: 1,
  };
  const urlWatch = watch({ url: "http://testapp.local/" });
  const graceWatch = watch({
    kind: "container",
    url: null,
    container: "shop-web-1",
    failures: null,
    failures_to_open: null,
    opens_after: "2026-11-02T19:01:00Z",
  });

  it("names a service whose URL failed once, and says the next failure opens an incident", () => {
    const { container } = render(
      <OverviewView
        hosts={[host({ health: "down" })]}
        services={[
          service({
            name: "bleep-testapp",
            health: "down",
            urls: [failingUrl],
            watching: [urlWatch],
          }),
        ]}
        incidents={[]}
        now={NOW}
      />,
    );
    expect(headline()).toHaveTextContent("Watching bleep-testapp: its URL check failed once.");
    expect(hero()).toHaveTextContent("If the next check fails too, I will look into it.");
    expect(within(hero()).getByText("http://testapp.local/ · 503")).toHaveClass("font-mono");
    expect(within(hero()).getByText("Watching")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Show me the service/ })).toHaveAttribute(
      "href",
      "/services/demo%2Fcompose%3Ashop",
    );
    expect(hero()).not.toHaveTextContent("Nothing needs you");
    expect(mood(container)).toBe("watching");
  });

  it("names a service whose container died inside its grace window, and until when", () => {
    const { container } = render(
      <OverviewView
        hosts={[host({ health: "down" })]}
        services={[
          service({ health: "down", containers: [deadContainer], watching: [graceWatch] }),
        ]}
        incidents={[]}
        now={NOW}
      />,
    );
    expect(headline()).toHaveTextContent("Watching shop: a container died.");
    expect(hero()).toHaveTextContent(/If it is not back by \d\d:\d\d[^,]*, I will look into it\./);
    expect(within(hero()).getByText("shop-web-1 · exited (1)")).toBeInTheDocument();
    expect(mood(container)).toBe("watching");
  });

  it("names every service it watches", () => {
    render(
      <OverviewView
        hosts={[host({ health: "down" })]}
        services={[
          service({ health: "down", urls: [failingUrl], watching: [urlWatch] }),
          service({
            id: "demo/compose:blog",
            name: "blog",
            health: "down",
            watching: [graceWatch],
          }),
        ]}
        incidents={[]}
        now={NOW}
      />,
    );
    expect(headline()).toHaveTextContent("Watching shop and blog.");
    expect(hero()).toHaveTextContent("I will look into each one that keeps failing.");
  });

  it("follows the incident instead once one is open for the service", () => {
    const { container } = render(
      <OverviewView
        hosts={[host({ health: "down" })]}
        services={[
          service({
            health: "down",
            urls: [failingUrl],
            open_incident_id: "inc-1",
            watching: [urlWatch],
          }),
        ]}
        incidents={[incident({ status: "investigating" })]}
        now={NOW}
      />,
    );
    expect(headline()).toHaveTextContent("shop is down. I am on it.");
    expect(mood(container)).toBe("working");
  });

  it("stays all good for a quiet service, and for a host that is fine", () => {
    const quiet = { scope: "service", mode: "snooze", until: null, since: "x" } as const;
    const { container } = render(
      <OverviewView
        hosts={[host(), host({ id: "spare", name: "Spare M1" })]}
        services={[
          service({ health: "down", urls: [failingUrl], quiet, watching: [urlWatch] }),
          service({ id: "spare/compose:blog", host_id: "spare", name: "blog" }),
        ]}
        incidents={[]}
        now={NOW}
      />,
    );
    expect(headline()).toHaveTextContent("Nothing needs you right now.");
    expect(mood(container)).toBe("all_good");
  });

  it("says it in German", () => {
    act(() => setLanguage("de"));
    render(
      <OverviewView
        hosts={[host({ health: "down" })]}
        services={[service({ health: "down", urls: [failingUrl], watching: [urlWatch] })]}
        incidents={[]}
        now={NOW}
      />,
    );
    expect(headline()).toHaveTextContent(
      "Ich beobachte shop: seine URL-Prüfung ist einmal fehlgeschlagen.",
    );
    expect(hero()).toHaveTextContent(
      "Schlägt auch die nächste Prüfung fehl, sehe ich ihn mir genauer an.",
    );
    expect(within(hero()).getByText("Unter Beobachtung")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Dienst ansehen/ })).toBeInTheDocument();
  });
});

describe("OverviewView hero while Bleep points at a service (#478)", () => {
  const dead = { ...service().containers[0], state: "exited", exit_code: 1 };
  const unwatched = (reason: UnwatchedReason, overrides: Partial<UnwatchedView> = {}) => ({
    reason,
    container: "shop-web-1",
    incident_id: null,
    incident_short_name: null,
    ...overrides,
  });
  const shownDown = (overrides: Partial<ServiceView> = {}) =>
    service({ health: "down", containers: [dead], ...overrides });

  it.each([
    ["paused", { ...dead, state: "paused", exit_code: null }, "It is paused.", "paused"],
    [
      "created",
      { ...dead, state: "created", exit_code: null },
      "It was created but never started.",
      "created",
    ],
    ["before_start", dead, "It was already down when I started.", "exited (1)"],
  ] as const)("says it opens nothing for a container %s, and why", (reason, box, line, state) => {
    const { container } = render(
      <OverviewView
        hosts={[host({ health: "down" })]}
        services={[shownDown({ containers: [box], unwatched: unwatched(reason) })]}
        incidents={[]}
        now={NOW}
      />,
    );
    expect(headline()).toHaveTextContent("shop is down, and I am not looking into it.");
    expect(hero()).toHaveTextContent(line);
    expect(within(hero()).getByText(`shop-web-1 · ${state}`)).toHaveClass("font-mono");
    expect(within(hero()).getByText("Your call")).toBeInTheDocument();
    expect(within(hero()).getByRole("button", { name: "Ask Bleep to look" })).toBeInTheDocument();
    expect(hero()).not.toHaveTextContent("Nothing needs you");
    expect(hero()).not.toHaveTextContent("I will look into it");
    expect(mood(container)).toBe("pointing");
  });

  it("names and links the incident it stayed down through", () => {
    render(
      <OverviewView
        hosts={[host({ health: "down" })]}
        services={[
          shownDown({
            unwatched: unwatched("after_incident", {
              incident_id: "inc_14",
              incident_short_name: "shop #14",
            }),
          }),
        ]}
        incidents={[incident({ id: "inc_14", status: "failed", short_name: "shop #14" })]}
        now={NOW + 2 * 24 * 60 * 60 * 1000}
      />,
    );
    expect(headline()).toHaveTextContent("shop is down, and I am not looking into it.");
    expect(hero()).toHaveTextContent("It is still down after shop #14 ended.");
    expect(within(hero()).getByRole("link", { name: "shop #14" })).toHaveAttribute(
      "href",
      "/incidents/inc_14",
    );
  });

  it("names an incident from before short names as its last one", () => {
    render(
      <OverviewView
        hosts={[host({ health: "down" })]}
        services={[shownDown({ unwatched: unwatched("after_incident", { incident_id: "old" }) })]}
        incidents={[]}
        now={NOW}
      />,
    );
    expect(hero()).toHaveTextContent("It is still down after its last incident ended.");
    expect(within(hero()).getByRole("link", { name: "its last incident" })).toHaveAttribute(
      "href",
      "/incidents/old",
    );
  });

  it("promises nothing when it has no reason, and still never says nothing needs you", () => {
    const { container } = render(
      <OverviewView
        hosts={[host({ health: "down" })]}
        services={[shownDown({ unwatched: null })]}
        incidents={[incident({ status: "closed" })]}
        now={NOW}
      />,
    );
    expect(headline()).toHaveTextContent("shop is down, and I am not looking into it.");
    expect(hero()).toHaveTextContent(
      "I did not see it go down, so I will not look into it on my own.",
    );
    expect(within(hero()).getByText("shop-web-1 · exited (1)")).toBeInTheDocument();
    expect(mood(container)).toBe("pointing");
  });

  it("shows the failing URL for a service down by its URL only, not a finished job", () => {
    const job = { ...dead, name: "shop-migrate-1", exit_code: 0 };
    const running = service().containers[0];
    const failing = { ...service().urls[0], status: "failing", status_code: 503 } as const;
    render(
      <OverviewView
        hosts={[host({ health: "down" })]}
        services={[
          shownDown({
            containers: [running, job],
            urls: [failing],
            unwatched: unwatched("after_incident", {
              container: null,
              incident_id: "inc_14",
              incident_short_name: "shop #14",
            }),
          }),
        ]}
        incidents={[]}
        now={NOW}
      />,
    );
    expect(within(hero()).getByText("http://localhost:8080/ · 503")).toHaveClass("font-mono");
    expect(hero()).not.toHaveTextContent("shop-migrate-1");
  });

  it("names every service it points at", () => {
    render(
      <OverviewView
        hosts={[host({ health: "down" })]}
        services={[
          shownDown({ unwatched: unwatched("before_start") }),
          shownDown({ id: "demo/compose:blog", name: "blog", unwatched: unwatched("paused") }),
        ]}
        incidents={[]}
        now={NOW}
      />,
    );
    expect(headline()).toHaveTextContent("shop and blog are down, and I am not looking into them.");
    expect(within(hero()).getByRole("button", { name: "Ask Bleep to look" })).toBeInTheDocument();
  });

  it("watches first: a service the watcher waits on comes before one it points at", () => {
    const { container } = render(
      <OverviewView
        hosts={[host({ health: "down" })]}
        services={[
          shownDown({ unwatched: unwatched("paused") }),
          shownDown({ id: "demo/compose:blog", name: "blog", watching: [watch()] }),
        ]}
        incidents={[]}
        now={NOW}
      />,
    );
    expect(headline()).toHaveTextContent("Watching blog");
    expect(mood(container)).toBe("watching");
  });

  it("shows a container stopped on purpose as stopped, with nothing to point at", () => {
    const { container } = render(
      <OverviewView
        hosts={[host()]}
        services={[service({ health: "stopped", containers: [{ ...dead, exit_code: 137 }] })]}
        incidents={[]}
        now={NOW}
      />,
    );
    expect(headline()).toHaveTextContent("Nothing needs you right now.");
    expect(mood(container)).toBe("all_good");
  });

  it("says it in German", () => {
    act(() => setLanguage("de"));
    render(
      <OverviewView
        hosts={[host({ health: "down" })]}
        services={[shownDown({ unwatched: unwatched("before_start") })]}
        incidents={[]}
        now={NOW}
      />,
    );
    expect(headline()).toHaveTextContent("shop ist ausgefallen, und ich sehe ihn mir nicht an.");
    expect(hero()).toHaveTextContent("Er war schon ausgefallen, als ich gestartet bin.");
    expect(within(hero()).getByText("Deine Entscheidung")).toBeInTheDocument();
    expect(
      within(hero()).getByRole("button", { name: "Bleep nachsehen lassen" }),
    ).toBeInTheDocument();
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

  it("links each host to its page and its services to theirs, and tints a host in trouble", () => {
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
    expect(within(demo).getByRole("link", { name: "Demo stack" })).toHaveAttribute(
      "href",
      "/hosts/demo",
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

describe("OverviewView quiet", () => {
  const snoozed = {
    scope: "service",
    mode: "snooze",
    until: new Date(2026, 10, 4, 18, 0).toISOString(),
    since: "2026-11-04T10:00:00Z",
  } as const;

  function atNoon() {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 10, 4, 12, 0));
  }

  afterEach(() => vi.useRealTimers());

  it("a quiet service row shows Snoozed until 18:00 instead of Down and has a Quiet button", () => {
    atNoon();
    render(
      <OverviewView
        hosts={[host({ health: "down", quiet_service_ids: ["demo/compose:shop"] })]}
        services={[service({ health: "down", quiet: snoozed })]}
        incidents={[]}
        now={NOW}
      />,
    );
    const row = screen.getByRole("list", { name: "Services on Demo stack" });
    expect(within(row).getByText("Snoozed until 18:00")).toBeInTheDocument();
    expect(within(row).queryByText("Down")).toBeNull();
    expect(screen.getByRole("button", { name: "Quiet shop" })).toBeInTheDocument();
  });

  it("shows a host that may sleep and does not answer as asleep, not unreachable", () => {
    const asleep = host({
      reachable: false,
      may_sleep: true,
      health: "asleep",
      error: "ssh unavailable: not connected",
    });
    render(<OverviewView hosts={[asleep]} services={[]} incidents={[]} now={NOW} />);
    const tile = screen.getByRole("region", { name: "Demo stack" });
    expect(within(tile).getByText("Asleep")).toBeInTheDocument();
    expect(within(tile).queryByText("Unreachable")).toBeNull();
    expect(tile).toHaveAttribute("data-hurting", "false");
    expect(within(tile).getByText("ssh unavailable: not connected")).not.toHaveClass("text-down");
  });

  it("shows a finished job as done, and its host is not hurting", () => {
    render(
      <OverviewView
        hosts={[host()]}
        services={[service({ health: "done" })]}
        incidents={[]}
        now={NOW}
      />,
    );
    const row = screen.getByRole("list", { name: "Services on Demo stack" });
    expect(within(row).getByText("Done")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Demo stack" })).toHaveAttribute(
      "data-hurting",
      "false",
    );
  });

  it("shows the services of an asleep host as asleep, not down", () => {
    const asleep = host({ reachable: false, may_sleep: true, health: "asleep" });
    render(
      <OverviewView
        hosts={[asleep]}
        services={[service({ health: "asleep" })]}
        incidents={[]}
        now={NOW}
      />,
    );
    const row = screen.getByRole("list", { name: "Services on Demo stack" });
    expect(within(row).getByText("Asleep")).toBeInTheDocument();
    expect(within(row).queryByText("Down")).toBeNull();
    expect(screen.getByRole("region", { name: "Demo stack" })).toHaveAttribute(
      "data-hurting",
      "false",
    );
  });

  it("a quiet unreachable host's error is not printed in the down colour", () => {
    const quiet = { scope: "host", mode: "leave_alone", until: null, since: "x" } as const;
    const down = host({ reachable: false, error: "ssh: timed out", health: "down" });
    const errorLine = () =>
      within(screen.getByRole("region", { name: "Demo stack" })).getByText("ssh: timed out");
    const { rerender } = render(
      <OverviewView hosts={[down]} services={[]} incidents={[]} now={NOW} />,
    );
    expect(errorLine()).toHaveClass("text-down");
    rerender(<OverviewView hosts={[{ ...down, quiet }]} services={[]} incidents={[]} now={NOW} />);
    expect(errorLine()).not.toHaveClass("text-down");
    expect(errorLine()).toHaveClass("text-ink-2");
  });

  it("a quiet host's tile is not tinted and shows Host left alone on its services", () => {
    render(
      <OverviewView
        hosts={[
          host({
            health: "down",
            quiet: { scope: "host", mode: "leave_alone", until: null, since: "x" },
          }),
        ]}
        services={[
          service({
            health: "down",
            quiet: { scope: "host", mode: "leave_alone", until: null, since: "x" },
          }),
        ]}
        incidents={[]}
        now={NOW}
      />,
    );
    const tile = screen.getByRole("region", { name: "Demo stack" });
    expect(tile).toHaveAttribute("data-hurting", "false");
    const list = within(tile).getByRole("list", { name: "Services on Demo stack" });
    expect(within(list).getByText("Host left alone")).toBeInTheDocument();
    expect(within(list).queryByText("Down")).toBeNull();
    expect(screen.getByRole("button", { name: "Quiet Demo stack" })).toBeInTheDocument();
  });

  it("choosing Leave it alone on a row reads the overview again", async () => {
    stubBackend({
      "PUT /api/quiet/services/demo/compose%3Ashop": json({
        scope: "service",
        mode: "leave_alone",
        until: null,
        since: "x",
      }),
    });
    const onChanged = vi.fn();
    render(
      <OverviewView
        hosts={[host()]}
        services={[service()]}
        incidents={[]}
        now={NOW}
        onChanged={onChanged}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Quiet shop" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: /Leave it alone/ }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
  });

  it("the hero rests when the only failed incident is about a quiet service", () => {
    const { container } = render(
      <OverviewView
        hosts={[host({ quiet_service_ids: ["demo/compose:shop"] })]}
        services={[service({ quiet: snoozed })]}
        incidents={[
          incident({
            status: "failed",
            closed_at: "2026-11-02T18:30:00Z",
            opened_at: "2026-11-02T18:00:00Z",
          }),
        ]}
        now={NOW}
      />,
    );
    expect(mood(container)).toBe("all_good");
  });
});

describe("OverviewView in German", () => {
  const german = () => act(() => setLanguage("de"));

  it("says what failed and what the plan is, with the targets in mono", () => {
    german();
    render(
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
                  { action: "docker.stop", host_id: "demo", target: "shop-worker-1", params: {} },
                ],
              }),
            }),
          }),
        }}
        now={NOW}
      />,
    );
    expect(headline()).toHaveTextContent(
      "shop ist ausgefallen. Ich weiß, warum, und ich habe eine Behebung.",
    );
    expect(screen.getByText(/Ich möchte/)).toHaveTextContent(
      "Ich möchte shop-db starten, danach shop-web-1 neu starten, und noch 1 weiteren Schritt ausführen. Ich fasse nichts an, bis du Ja sagst.",
    );
    expect(screen.getByText("shop-db").tagName).toBe("CODE");
    expect(screen.getByRole("link", { name: "Plan ansehen" })).toHaveAttribute(
      "href",
      "/incidents/inc-1",
    );
    expect(screen.getByText("Braucht dein Ja")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Deine Hosts" })).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Dienste auf Demo stack" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Vorfall zu shop öffnen" })).toBeInTheDocument();
    expect(screen.getByText("Demo stack hat Probleme: sein einziger Dienst.")).toBeInTheDocument();
  });

  it("counts the other incidents that need the human", () => {
    german();
    render(
      <OverviewView
        hosts={[host({ health: "down" })]}
        services={[service({ health: "down" })]}
        incidents={[
          incident({ id: "a", status: "awaiting_approval" }),
          incident({ id: "b", status: "awaiting_approval" }),
          incident({ id: "c", status: "awaiting_approval" }),
        ]}
        now={NOW}
      />,
    );
    expect(
      screen.getByRole("link", { name: "2 weitere Vorfälle brauchen dich" }),
    ).toBeInTheDocument();
  });

  it("names an unreachable host, when it last answered and what it cannot see", () => {
    german();
    render(
      <OverviewView
        hosts={[
          host({
            id: "main",
            name: "coolify-main",
            kind: "ssh",
            reachable: false,
            health: "down",
            last_seen: "2026-11-02T18:00:00Z",
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
    expect(headline()).toHaveTextContent("Ich erreiche coolify-main nicht.");
    expect(
      screen.getByText(
        /Zuletzt habe ich am 02\.11\.2026, \d\d:\d\d von ihm gehört, deshalb sehe ich molecula und blog nicht\./,
      ),
    ).toBeInTheDocument();
    expect(within(hero()).getByText("Nicht erreichbar")).toBeInTheDocument();
    expect(within(hero()).getByText("ssh: connection timed out")).toBeInTheDocument();
  });

  it("says nothing needs the human, and counts what it watches", () => {
    german();
    render(
      <OverviewView
        hosts={[host(), host({ id: "spare", name: "Spare M1" })]}
        services={[service()]}
        incidents={[]}
        now={NOW}
      />,
    );
    expect(headline()).toHaveTextContent("Gerade braucht dich nichts.");
    expect(screen.getByText("Ich beobachte 2 Hosts und 1 Dienst.")).toBeInTheDocument();
    expect(screen.getByText("Alles ruhig")).toBeInTheDocument();
    expect(screen.getByText("Beide sind in Ordnung.")).toBeInTheDocument();
  });

  it("shows a host's basics with a decimal comma", () => {
    german();
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
              disks: [{ mount: "/", used_percent: 58 }],
            },
          }),
        ]}
        services={[service()]}
        incidents={[]}
        now={NOW}
      />,
    );
    expect(screen.getByText("Last 0,3 · RAM 75 % · Platte 58 %")).toBeInTheDocument();
  });

  it("shows the empty state and the quiet state in German", () => {
    german();
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 10, 4, 12, 0));
    const { rerender } = render(<OverviewView hosts={[]} services={[]} incidents={[]} now={NOW} />);
    expect(headline()).toHaveTextContent("Ich habe noch keinen Host, den ich beobachten kann.");
    expect(screen.getByText("Noch keine Hosts.")).toBeInTheDocument();
    expect(screen.getByText(/config\/hosts\.yaml/).closest("p")).toHaveTextContent(
      "Oder trage einen in config/hosts.yaml ein.",
    );
    expect(screen.getByRole("link", { name: "Einrichtungsanleitung" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Host hinzufügen" })).not.toHaveLength(0);

    rerender(
      <OverviewView
        hosts={[host({ health: "down", quiet_service_ids: ["demo/compose:shop"] })]}
        services={[
          service({
            health: "down",
            quiet: {
              scope: "service",
              mode: "snooze",
              until: new Date(2026, 10, 4, 18, 0).toISOString(),
              since: "2026-11-04T10:00:00Z",
            },
          }),
        ]}
        incidents={[]}
        now={NOW}
      />,
    );
    expect(screen.getByText("Stumm bis 18:00")).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("lets a long German word in the headline wrap and hyphenate instead of widening the page", () => {
    german();
    render(
      <OverviewView
        hosts={[
          host({
            id: "main",
            name: "Donaudampfschifffahrtsgesellschaftskapitän",
            reachable: false,
            health: "down",
          }),
        ]}
        services={[]}
        incidents={[]}
        now={NOW}
      />,
    );
    expect(headline()).toHaveTextContent(
      "Ich erreiche Donaudampfschifffahrtsgesellschaftskapitän nicht.",
    );
    expect(headline()).toHaveClass("wrap-anywhere", "hyphens-auto");
    expect(headline().parentElement).toHaveClass("min-w-0");
  });
});
