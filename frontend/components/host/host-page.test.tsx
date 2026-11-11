import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HostPage, StillFailing } from "@/components/host/host-page";
import { incident } from "@/components/incident/fixtures";
import { OverallProvider } from "@/components/overall-state";
import {
  json,
  sentBody,
  settingsView,
  sshHost,
  stubBackend as stubSettingsBackend,
} from "@/components/settings/fixtures";
import type { HostView, IncidentListItem, ServiceView } from "@/lib/api-types";
import { setLanguage } from "@/lib/i18n";

const HOST: HostView = {
  id: "m1",
  name: "M1",
  kind: "ssh",
  reachable: true,
  last_seen: "2026-11-05T10:00:00Z",
  error: null,
  basics: {
    os: "Debian 13",
    cpus: 2,
    load1: 0.3,
    load15: 0.2,
    mem_total: 1000,
    mem_available: 250,
    disks: [],
  },
  service_count: 2,
  health: "ok",
  may_sleep: false,
  quiet: null,
  quiet_service_ids: [],
};

const router = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));

const SHOP: ServiceView = {
  id: "m1/compose:shop",
  host_id: "m1",
  name: "shop",
  source: "compose",
  health: "ok",
  containers: [],
  urls: [],
  open_incident_id: null,
  quiet: null,
};
const WORKER: ServiceView = { ...SHOP, id: "m1/compose:worker", name: "worker", health: "down" };
const ELSEWHERE: ServiceView = { ...SHOP, id: "m2/compose:blog", host_id: "m2", name: "blog" };

let hostList: HostView[] = [];

/** The header's overall poll (`OverallProvider`): the newest incidents and the hosts. */
function stubBackend(routes: Parameters<typeof stubSettingsBackend>[0]) {
  const inner = stubSettingsBackend(routes);
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input);
    if (url === "/api/hosts") return json(hostList);
    if (url.startsWith("/api/incidents?limit=")) return json([]);
    return inner(input, init);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const hostReads = (fetchMock: ReturnType<typeof stubBackend>) =>
  fetchMock.mock.calls.filter(([input]) => String(input) === "/api/hosts").length;

const HOST_INCIDENTS = "GET /api/incidents?host_id=m1&limit=1";

function backend(
  put: Response[] = [],
  view = settingsView(),
  hostIncidents: IncidentListItem[] = [],
) {
  return stubBackend({
    "GET /api/settings": json(view),
    "GET /api/services": json([SHOP, WORKER, ELSEWHERE]),
    "PUT /api/settings": put,
    [HOST_INCIDENTS]: json(hostIncidents),
  });
}

/** The host's own last incident, closed by the person at 12:05 while the host was still down. */
function hostIncident(overrides: Partial<IncidentListItem> = {}): IncidentListItem {
  return {
    ...incident({
      id: "inc-7",
      subject: { kind: "host", host_id: "m1", service_id: null },
      status: "closed",
      title: "host unreachable on m1",
      opened_at: "2026-11-05T11:05:00Z",
      closed_at: "2026-11-05T12:05:00Z",
    }),
    votes: { diagnosis: null, proposal: null },
    repeats: 1,
    kind: "host_unreachable",
    ...overrides,
  };
}

/** Down since 11:00: the host has not answered since before its incident ended. */
const DOWN: HostView = {
  ...HOST,
  reachable: false,
  health: "down",
  last_seen: "2026-11-05T11:00:00Z",
  error: "ssh: connection refused",
};

function renderPage(id = "m1", hosts: HostView[] = [HOST]) {
  hostList = hosts;
  return render(
    <OverallProvider>
      <HostPage id={id} />
    </OverallProvider>,
  );
}

describe("HostPage, still unreachable after its last incident (#362)", () => {
  it("says the host is still unreachable after its last incident ended, and links it", async () => {
    backend([], settingsView(), [hostIncident()]);
    renderPage("m1", [DOWN]);

    const note = await screen.findByRole("note", { name: "Still failing" });
    expect(note).toHaveTextContent("Still unreachable after its last incident ended");
    expect(within(note).getByRole("link", { name: "host unreachable on m1" })).toHaveAttribute(
      "href",
      "/incidents/inc-7",
    );
    expect(note).toHaveTextContent("a wait that grows with each repeat, from an hour to a day");
  });

  it("names the last incident by its short name in front of its title (#397)", () => {
    render(<StillFailing host={DOWN} last={hostIncident({ short_name: "M1 #3" })} />);
    const link = screen.getByRole("link", { name: /host unreachable on m1/ });
    expect(link).toHaveTextContent(/^M1 #3 · host unreachable on m1$/);
    expect(within(link).getByText("M1 #3")).toHaveClass("font-mono", "text-ink-2");
  });

  it("links the last incident by its title alone when it has no short name", () => {
    render(<StillFailing host={DOWN} last={hostIncident()} />);
    expect(screen.getByRole("link")).toHaveTextContent(/^host unreachable on m1$/);
  });

  it("says nothing while one is open, once it answers, when quiet or asleep, or with none", () => {
    const quiet = { scope: "host", mode: "leave_alone", until: null } as const;
    const cases: [string, HostView, IncidentListItem | undefined][] = [
      ["open", DOWN, hostIncident({ status: "waiting", closed_at: null })],
      ["answering", { ...DOWN, reachable: true, health: "ok" }, hostIncident()],
      // It answered after the incident opened: the watcher saw it pass, so this is a new outage.
      ["answered since", { ...DOWN, last_seen: "2026-11-05T11:30:00Z" }, hostIncident()],
      ["another kind", DOWN, hostIncident({ kind: "disk", title: "disk on m1" })],
      ["quiet", { ...DOWN, quiet: { ...quiet, since: "2026-11-05T10:00:00Z" } }, hostIncident()],
      ["asleep", { ...DOWN, may_sleep: true, health: "asleep" }, hostIncident()],
      ["none", DOWN, undefined],
    ];
    for (const [why, host, last] of cases) {
      const { container, unmount } = render(<StillFailing host={host} last={last} />);
      expect(container, why).toBeEmptyDOMElement();
      unmount();
    }
    const { container } = render(<StillFailing host={DOWN} last={hostIncident()} />);
    expect(container).not.toBeEmptyDOMElement(); // the cases above differ from it in one thing
  });
});

describe("HostPage", () => {
  it("shows the host's state and links its own services to their pages", async () => {
    backend();
    renderPage();

    expect(await screen.findByRole("heading", { level: 1, name: "M1" })).toBeInTheDocument();
    expect(screen.getByText("load 0.3 · mem 75%")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Quiet M1" })).toBeInTheDocument();
    const list = await screen.findByRole("list", { name: "Services on M1" });
    expect(within(list).getByRole("link", { name: /shop/ })).toHaveAttribute(
      "href",
      "/services/m1%2Fcompose%3Ashop",
    );
    expect(within(list).getByRole("link", { name: /worker/ })).toHaveTextContent("Down");
    expect(within(list).queryByText("blog")).toBeNull();
  });

  it("lets a long unbroken host name wrap instead of widening the page", async () => {
    backend();
    const name = "h".repeat(50);
    renderPage(name, [{ ...HOST, id: name, name }]);
    expect(await screen.findByRole("heading", { level: 1, name })).toHaveClass("wrap-anywhere");
  });

  it("shows an unreachable host's error", async () => {
    backend();
    renderPage("m1", [{ ...HOST, reachable: false, error: "ssh: connection refused" }]);
    expect(await screen.findByText("Unreachable")).toBeInTheDocument();
    expect(screen.getByText("ssh: connection refused")).toBeInTheDocument();
  });

  it("shows a host that may sleep and does not answer as asleep", async () => {
    backend();
    renderPage("m1", [
      { ...HOST, reachable: false, may_sleep: true, health: "asleep", error: "ssh: no route" },
    ]);
    expect(await screen.findByText("Asleep")).toBeInTheDocument();
    expect(screen.queryByText("Unreachable")).toBeNull();
    expect(screen.getByText("ssh: no route")).not.toHaveClass("text-down");
  });

  it("lets the host be marked as one that may sleep", async () => {
    const fetchMock = backend([json(settingsView({ version: "v2" }))]);
    renderPage();
    const sleep = await screen.findByRole("region", { name: "Sleep" });
    await userEvent.click(within(sleep).getByRole("checkbox", { name: "This host may sleep" }));

    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(true),
    );
    expect(sentBody(fetchMock, "PUT").hosts.m1.may_sleep).toBe(true);
  });

  it("says when Bleep does not know the host, with a way home", async () => {
    backend();
    renderPage("nope");
    expect(await screen.findByText(/Bleep does not know a host called/)).toHaveTextContent("nope");
    expect(screen.getByRole("link", { name: "Back to your hosts" })).toHaveAttribute("href", "/");
    expect(screen.queryByRole("region", { name: "Connection" })).toBeNull();
  });

  it("shows a hosts file host's connection read-only, with no edit or remove", async () => {
    backend();
    renderPage();
    const connection = await screen.findByRole("region", { name: "Connection" });
    expect(
      within(connection).getByText("From config/hosts.yaml; change it there"),
    ).toBeInTheDocument();
    expect(within(connection).getByText("danny@m1.local:22")).toBeInTheDocument();
    expect(within(connection).getByText("file /keys/id_ed25519")).toBeInTheDocument();
    expect(within(connection).queryByRole("button", { name: "Edit connection" })).toBeNull();
    expect(within(connection).queryByRole("button", { name: "Remove" })).toBeNull();
  });

  it("goes back to the overview once a dashboard host is removed", async () => {
    const base = sshHost({ services: {} });
    const dashboard = {
      ...base,
      connection: {
        ...base.connection,
        source: "dashboard" as const,
        key_file: null,
        ssh_key: { env: null, file: null, set: true },
        host_key: { env: null, file: null, set: false },
        version: "v-m1",
      },
    };
    const fetchMock = stubBackend({
      "GET /api/settings": json(settingsView({ hosts: [dashboard], overrides: {} })),
      "GET /api/services": json([]),
      "DELETE /api/settings/hosts/m1": new Response(null, { status: 204 }),
    });
    renderPage();
    const connection = await screen.findByRole("region", { name: "Connection" });
    expect(hostReads(fetchMock)).toBe(1);
    expect(within(connection).getByText("Key: set")).toBeInTheDocument();
    expect(within(connection).getByText("Host key: missing")).toBeInTheDocument();

    await userEvent.click(within(connection).getByRole("button", { name: "Remove" }));
    await userEvent.click(
      within(connection).getByRole("button", { name: "Yes, remove this host" }),
    );
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/"));
    await waitFor(() => expect(hostReads(fetchMock)).toBe(2));
    const removal = fetchMock.mock.calls.find(([, init]) => init?.method === "DELETE");
    expect(removal?.[1]?.headers).toMatchObject({ "if-match": '"v-m1"' });
  });

  it("lets a dashboard host be marked as one that may sleep, with no hosts file to go back to", async () => {
    const base = sshHost({ services: {} });
    const dashboard = {
      ...base,
      connection: { ...base.connection, source: "dashboard" as const, version: "v-m1" },
    };
    const fetchMock = stubBackend({
      "GET /api/settings": json(settingsView({ hosts: [dashboard], overrides: {} })),
      "GET /api/services": json([]),
      "PUT /api/settings": [json(settingsView({ hosts: [{ ...dashboard, may_sleep: true }] }))],
    });
    renderPage();
    const sleep = await screen.findByRole("region", { name: "Sleep" });
    expect(within(sleep).queryByText("From the hosts file")).toBeNull();
    await userEvent.click(within(sleep).getByRole("checkbox", { name: "This host may sleep" }));

    await waitFor(() =>
      expect(within(sleep).getByRole("checkbox", { name: "This host may sleep" })).toBeChecked(),
    );
    expect(sentBody(fetchMock, "PUT")).toEqual({ hosts: { m1: { may_sleep: true } } });
  });

  it("shows a refusal for a secret in a rule as the API words it", async () => {
    backend([
      json(
        {
          detail:
            "settings never hold secrets; hosts.m1.rules.1 looks like one. Secrets go in the environment or a mounted file named in the hosts file",
        },
        422,
      ),
    ]);
    renderPage();
    const host = await screen.findByRole("region", { name: "Rules for every service" });

    await userEvent.type(
      within(host).getByPlaceholderText("Add a rule for this host"),
      "token ghp_0123456789abcdefghij0123456789abcdef{Enter}",
    );
    await userEvent.click(within(host).getByRole("button", { name: "Save" }));

    expect(await within(host).findByRole("alert")).toHaveTextContent(
      "settings never hold secrets; hosts.m1.rules.1 looks like one.",
    );
  });

  it("sends the version the save was built from", async () => {
    const fetchMock = backend([json(settingsView({ version: "v2" }))]);
    renderPage();
    const host = await screen.findByRole("region", { name: "Rules for every service" });
    await userEvent.type(within(host).getByPlaceholderText("Add a rule for this host"), "r{Enter}");
    await userEvent.click(within(host).getByRole("button", { name: "Save" }));

    const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
    expect(put?.[1]?.headers).toMatchObject({ "if-match": '"v1"' });
  });

  it("says the settings could not be read again after a 409, rather than that they were", async () => {
    stubBackend({
      "GET /api/settings": [json(settingsView()), json({ detail: "Bad Gateway" }, 502)],
      "GET /api/services": json([SHOP]),
      "PUT /api/settings": [json({ detail: "the settings changed", version: "v2" }, 409)],
    });
    renderPage();
    const host = await screen.findByRole("region", { name: "Rules for every service" });
    await userEvent.type(within(host).getByPlaceholderText("Add a rule for this host"), "r{Enter}");
    await userEvent.click(within(host).getByRole("button", { name: "Save" }));

    const alert = (await screen.findAllByRole("alert"))[0];
    expect(alert).toHaveTextContent("Reading them again failed (Bad Gateway)");
    expect(alert).not.toHaveTextContent("have been read again");
  });

  it("says when the services cannot be loaded and still links those with settings", async () => {
    stubBackend({
      "GET /api/settings": json(settingsView()),
      "GET /api/services": json({ detail: "the registry is down" }, 503),
    });
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load the services: the registry is down",
    );
    const list = await screen.findByRole("list", { name: "Services on M1" });
    const shop = within(list).getByRole("link", { name: /compose:shop/ });
    expect(shop).toHaveAttribute("href", "/services/m1%2Fcompose%3Ashop");
    expect(shop).not.toHaveTextContent("Not seen now");
  });

  it("links a service with settings that Bleep does not see now, saying so", async () => {
    const view = settingsView();
    view.hosts[0].services["compose:old"] = view.hosts[0].services["compose:shop"];
    backend([], view);
    renderPage();
    const list = await screen.findByRole("list", { name: "Services on M1" });
    const old = await within(list).findByRole("link", { name: /compose:old/ });
    expect(old).toHaveAttribute("href", "/services/m1%2Fcompose%3Aold");
    expect(old).toHaveTextContent("Not seen now");
    expect(within(list).getAllByRole("link")).toHaveLength(3);
  });

  it("takes a service the hosts file names by its name for the one Bleep sees", async () => {
    const view = settingsView();
    view.hosts[0].services = { shop: view.hosts[0].services["compose:shop"] };
    backend([], view);
    renderPage();
    const list = await screen.findByRole("list", { name: "Services on M1" });
    await screen.findByRole("region", { name: "Rules for every service" });
    expect(within(list).getAllByRole("link")).toHaveLength(2);
    expect(within(list).queryByText("Not seen now")).toBeNull();
  });

  it("edits no service's settings here", async () => {
    backend();
    renderPage();
    await screen.findByRole("region", { name: "Rules for every service" });
    expect(screen.queryByRole("region", { name: "Service settings" })).toBeNull();
    expect(screen.queryByRole("textbox", { name: "URL 1" })).toBeNull();
  });
});

describe("HostPage in German", () => {
  beforeEach(() => act(() => setLanguage("de")));

  it("speaks German on the page, its services and its states", async () => {
    backend();
    renderPage("m1", [{ ...HOST, reachable: false, error: "ssh: connection refused" }]);
    const title = await screen.findByRole("heading", { level: 1, name: "M1" });
    expect(title).toHaveClass("wrap-anywhere", "hyphens-auto");
    expect(screen.getByText("Nicht erreichbar")).toBeInTheDocument();
    expect(screen.getByText("ssh: connection refused")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Brotkrümelnavigation" })).toHaveTextContent(
      "Start",
    );
    expect(screen.getByText(/SSH-Host/)).toHaveTextContent("SSH-Host m1 · 2 Dienste");
    expect(screen.getByRole("heading", { name: "Dienste" })).toBeInTheDocument();
    const list = await screen.findByRole("list", { name: "Dienste auf M1" });
    expect(within(list).getByText("worker")).toBeInTheDocument();
    expect(within(list).getByText("Ausgefallen")).toBeInTheDocument();
    expect(screen.getByText(/^zuletzt gesehen am 05\.11\.2026, \d\d:\d\d$/)).toBeInTheDocument();
  });

  it("says since or until when a quiet host is quiet", async () => {
    backend();
    const until = new Date(2030, 0, 1, 18, 0).toISOString();
    const quiet = { scope: "host", mode: "snooze", until, since: "2026-11-05T10:00:00Z" } as const;
    renderPage("m1", [{ ...HOST, quiet }]);
    const note = await screen.findByRole("note");
    expect(note.textContent).toMatch(
      /^Bleep öffnet bis .*18:00 keine Vorfälle für ihn oder seine Dienste\.$/,
    );
    expect(
      screen.getByRole("button", { name: /^M1 stummschalten: Host stumm bis .*18:00/ }),
    ).toBeInTheDocument();
  });

  it("says it is still unreachable after the last incident, with German dates", () => {
    render(<StillFailing host={DOWN} last={hostIncident()} />);
    const note = screen.getByRole("note", { name: "Besteht weiterhin" });
    expect(note).toHaveTextContent(
      /^Weiterhin nicht erreichbar; sein letzter Vorfall endete .+: host unreachable on m1\./,
    );
    expect(note.querySelector("time")).toHaveAttribute(
      "title",
      expect.stringMatching(/^05\.11\.2026, \d\d:\d\d$/),
    );
    expect(note).toHaveTextContent(
      "Einen neuen öffnet Bleep nach einer Wartezeit, die mit jeder Wiederholung wächst, von einer Stunde bis zu einem Tag.",
    );
  });

  it("says when Bleep does not know the host", async () => {
    backend();
    renderPage("nope");
    expect(await screen.findByText(/Bleep kennt keinen Host namens/)).toHaveTextContent(
      "nope. Zurück zu deinen Hosts",
    );
    expect(screen.getByRole("heading", { level: 1, name: "nope" })).toHaveClass("hyphens-auto");
  });
});
