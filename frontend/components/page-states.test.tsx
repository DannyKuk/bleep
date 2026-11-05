import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DevPanel } from "@/components/dev/dev-panel";
import { HostPage } from "@/components/host/host-page";
import { IncidentList } from "@/components/incident/incident-list";
import { IncidentPage } from "@/components/incident/incident-page";
import { OverallProvider } from "@/components/overall-state";
import { Overview } from "@/components/overview/overview";
import { Recoveries } from "@/components/overview/recoveries";
import { ServicePage } from "@/components/service/service-page";
import { json, plugin, secretViews, settingsView, telegram } from "@/components/settings/fixtures";
import { SettingsPage } from "@/components/settings/settings-page";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

class SilentEventSource {
  onopen = null;
  onmessage = null;
  onerror = null;
  close() {}
}

type Answer = Response | "pending" | "down";
type Routes = (path: string) => Answer | undefined;

/** Answers each request from `routes`; "pending" never answers, "down" is a refused connection. */
function backend(routes: Routes) {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (input) => {
      const answer = routes(String(input)) ?? json([]);
      if (answer === "down") throw new TypeError("Failed to fetch");
      if (answer === "pending") return new Promise<Response>(() => undefined);
      return answer.clone();
    }),
  );
}

const withHeader = (page: React.ReactNode) => render(<OverallProvider>{page}</OverallProvider>);
const fiveHundred = () => json({ detail: "boom" }, 500);

beforeEach(() => vi.stubGlobal("EventSource", SilentEventSource));
afterEach(() => vi.unstubAllGlobals());

describe("the overview", () => {
  it("says it is loading while the first read is pending", () => {
    backend(() => "pending");
    withHeader(<Overview />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading hosts");
  });

  it("says so when the backend is down", async () => {
    backend(() => "down");
    withHeader(<Overview />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load the overview");
  });

  it("offers to add the first host when there is none", async () => {
    backend(() => undefined);
    withHeader(<Overview />);
    expect(await screen.findByText("No hosts yet.")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /Add a host/ }).length).toBeGreaterThan(0);
  });
});

describe("what fixed itself", () => {
  it("is loading, then empty", async () => {
    backend(() => "pending");
    const { unmount } = render(<Recoveries />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading what fixed itself");
    unmount();

    backend(() => undefined);
    render(<Recoveries />);
    expect(await screen.findByText(/Nothing has fixed itself yet/)).toBeInTheDocument();
  });

  it("says so when it cannot be read", async () => {
    backend(() => fiveHundred());
    render(<Recoveries />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load recoveries");
    expect(screen.queryByRole("status")).toBeNull();
  });
});

describe("the incidents list", () => {
  it("is loading, then empty", async () => {
    backend((path) => (path.startsWith("/api/incidents") ? "pending" : undefined));
    const { unmount } = withHeader(<IncidentList />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading incidents");
    unmount();

    backend(() => undefined);
    withHeader(<IncidentList />);
    expect(await screen.findByText(/No incidents yet/)).toBeInTheDocument();
  });

  it("says so when the backend is down", async () => {
    backend(() => "down");
    withHeader(<IncidentList />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load incidents");
  });
});

describe("an incident's page", () => {
  it("is loading, then says so when the backend is down", async () => {
    backend((path) => (path === "/api/incidents/inc-1" ? "pending" : undefined));
    const { unmount } = withHeader(<IncidentPage id="inc-1" />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading the incident");
    unmount();

    backend((path) => (path === "/api/incidents/inc-1" ? "down" : undefined));
    withHeader(<IncidentPage id="inc-1" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load the incident");
  });

  it("says there is no such incident for a 404", async () => {
    backend((path) =>
      path === "/api/incidents/inc-1" ? json({ detail: "Not Found" }, 404) : undefined,
    );
    withHeader(<IncidentPage id="inc-1" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("There is no incident");
  });
});

describe("a service's page", () => {
  const ID = "demo/compose:shop";
  const SERVICE_PATH = `/api/services/${encodeURIComponent(ID)}`;

  it("is loading while its reads are pending", () => {
    backend((path) => (path.startsWith("/api/services/") ? "pending" : undefined));
    withHeader(<ServicePage id={ID} />);
    expect(screen.getByText("Loading the service")).toBeInTheDocument();
  });

  it("says so when the backend is down", async () => {
    backend(() => "down");
    withHeader(<ServicePage id={ID} />);
    expect(await screen.findByText(/Could not load the service:/)).toBeInTheDocument();
    expect(await screen.findByText(/Could not load the incidents:/)).toBeInTheDocument();
  });

  it("says so for a service with no URLs, no facts and no incidents", async () => {
    backend((path) => {
      if (path !== SERVICE_PATH) return path === "/api/settings" ? json(settingsView()) : undefined;
      return json({
        service: {
          id: ID,
          host_id: "demo",
          name: "shop",
          source: "compose",
          health: "ok",
          containers: [],
          urls: [],
          open_incident_id: null,
          quiet: null,
        },
        rules: [],
        host_rules: [],
        health_since: null,
        recent_checks: [],
      });
    });
    withHeader(<ServicePage id={ID} />);
    expect(await screen.findByText("No URLs are checked.")).toBeInTheDocument();
    expect(await screen.findByText("No containers.")).toBeInTheDocument();
    expect(await screen.findByText(/No incidents yet/)).toBeInTheDocument();
    expect(await screen.findByText(/No facts yet/)).toBeInTheDocument();
  });
});

describe("a host's page", () => {
  it("is loading while the hosts are pending", () => {
    backend((path) => (path === "/api/hosts" ? "pending" : undefined));
    withHeader(<HostPage id="m1" />);
    expect(screen.getByText("Loading the host")).toBeInTheDocument();
  });

  it("says so when the backend is down", async () => {
    backend(() => "down");
    withHeader(<HostPage id="m1" />);
    expect(await screen.findByText(/Could not load the host:/)).toBeInTheDocument();
    expect(await screen.findByText(/Could not load the services:/)).toBeInTheDocument();
  });

  it("says Bleep sees no services on a host that has none", async () => {
    backend((path) => {
      if (path === "/api/hosts") return json([hostView()]);
      if (path === "/api/settings") return json(settingsView({ hosts: [] }));
      return undefined;
    });
    withHeader(<HostPage id="m1" />);
    expect(await screen.findByText(/Bleep sees no services on this host yet/)).toBeInTheDocument();
  });
});

function hostView() {
  return {
    id: "m1",
    name: "M1",
    kind: "ssh",
    reachable: true,
    last_seen: null,
    error: null,
    basics: null,
    service_count: 0,
    health: "ok",
    may_sleep: false,
    quiet: null,
    quiet_service_ids: [],
  };
}

describe("the settings page", () => {
  it("is loading while its reads are pending", () => {
    backend(() => "pending");
    render(<SettingsPage />);
    expect(screen.getAllByRole("status").length).toBeGreaterThan(0);
    expect(screen.getByText("Loading the settings")).toBeInTheDocument();
  });

  it("says so for every section when the backend is down", async () => {
    backend(() => "down");
    render(<SettingsPage />);
    expect(await screen.findByText(/Could not load the settings:/)).toBeInTheDocument();
    expect(await screen.findByText(/Could not load the plugins:/)).toBeInTheDocument();
  });

  it("says there are no hosts yet", async () => {
    backend((path) => {
      if (path === "/api/settings") return json(settingsView({ hosts: [] }));
      if (path === "/api/plugins") return json([plugin()]);
      if (path === "/api/telegram/status") return json(telegram());
      if (path === "/api/secrets") return json(secretViews());
      return undefined;
    });
    render(<SettingsPage />);
    expect(await screen.findByText("No hosts yet.")).toBeInTheDocument();
  });
});

describe("the dev panel", () => {
  it("is loading while the backend is pending", () => {
    backend(() => "pending");
    render(<DevPanel />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading the dev panel");
  });

  it("says so when the backend is down", async () => {
    backend(() => "down");
    render(<DevPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load the dev panel");
  });
});
