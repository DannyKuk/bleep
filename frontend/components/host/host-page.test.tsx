import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { HostPage } from "@/components/host/host-page";
import {
  invalid,
  json,
  sentBody,
  settingsView,
  sshHost,
  stubBackend,
} from "@/components/settings/fixtures";
import type { HostView, ServiceView } from "@/lib/api-types";
import { SETTINGS_CHANGED } from "@/lib/settings-api";

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
  quiet: null,
  quiet_service_ids: [],
};

const overall = vi.hoisted(() => ({
  hosts: [] as HostView[] | undefined,
  refresh: vi.fn(),
}));
vi.mock("@/components/overall-state", () => ({ useOverall: () => overall }));

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

function backend(put: Response[] = [], view = settingsView()) {
  return stubBackend({
    "GET /api/settings": json(view),
    "GET /api/services": json([SHOP, WORKER, ELSEWHERE]),
    "PUT /api/settings": put,
  });
}

function renderPage(id = "m1", hosts: HostView[] = [HOST]) {
  overall.hosts = hosts;
  return render(<HostPage id={id} />);
}

async function shopSection() {
  return screen.findByRole("region", { name: "Service shop" });
}

describe("HostPage", () => {
  it("shows the host's state and links its own services to their pages", async () => {
    backend();
    renderPage();

    expect(screen.getByRole("heading", { level: 1, name: "M1" })).toBeInTheDocument();
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

  it("shows an unreachable host's error", async () => {
    backend();
    renderPage("m1", [{ ...HOST, reachable: false, error: "ssh: connection refused" }]);
    expect(screen.getByText("Unreachable")).toBeInTheDocument();
    expect(screen.getByText("ssh: connection refused")).toBeInTheDocument();
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
    expect(within(connection).getByText("Key: set")).toBeInTheDocument();
    expect(within(connection).getByText("Host key: missing")).toBeInTheDocument();

    await userEvent.click(within(connection).getByRole("button", { name: "Remove" }));
    await userEvent.click(
      within(connection).getByRole("button", { name: "Yes, remove this host" }),
    );
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/"));
    expect(overall.refresh).toHaveBeenCalled();
    const removal = fetchMock.mock.calls.find(([, init]) => init?.method === "DELETE");
    expect(removal?.[1]?.headers).toMatchObject({ "if-match": '"v-m1"' });
  });

  it("says when the services cannot be loaded and still lists those with settings", async () => {
    stubBackend({
      "GET /api/settings": json(settingsView()),
      "GET /api/services": json({ detail: "the registry is down" }, 503),
    });
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load the services: the registry is down",
    );
    expect(await screen.findByRole("region", { name: "Service compose:shop" })).toBeInTheDocument();
  });

  it("saves a service's URLs as that host's overrides and keeps the host's others", async () => {
    const ready = {
      url: "https://shop.example.test/ready",
      expect_status: 204,
      body_contains: null,
    };
    const saved = settingsView();
    saved.hosts[0].services["compose:shop"].urls = [ready];
    saved.overrides.hosts = {
      m1: {
        services: { "compose:shop": { protected_containers: ["shop-cache-1"], urls: [ready] } },
      },
    };
    const fetchMock = backend([json(saved)]);
    renderPage();
    const shop = await shopSection();

    const url = within(shop).getByRole("textbox", { name: "URL 1" });
    await userEvent.clear(url);
    await userEvent.type(url, "https://shop.example.test/ready");
    const status = within(shop).getByRole("textbox", { name: "Expected status 1" });
    await userEvent.clear(status);
    await userEvent.type(status, "204");
    await userEvent.click(within(shop).getByRole("button", { name: "Save" }));

    expect(sentBody(fetchMock, "PUT")).toEqual({
      hosts: {
        m1: {
          services: {
            "compose:shop": {
              protected_containers: ["shop-cache-1"],
              urls: [ready],
            },
          },
        },
      },
    });
    expect(await within(shop).findByDisplayValue("https://shop.example.test/ready")).toBeVisible();
    expect(within(shop).getByText("Changed here")).toBeInTheDocument();
  });

  it("shows the API's reason when it refuses a URL, and keeps the draft", async () => {
    backend([
      invalid(
        ["hosts", "m1", "services", "compose:shop", "urls"],
        "Value error, invalid url: must be http:// or https:// with a host and a valid port",
      ),
    ]);
    renderPage();
    const shop = await shopSection();

    const url = within(shop).getByRole("textbox", { name: "URL 1" });
    await userEvent.clear(url);
    await userEvent.type(url, "ftp://shop");
    await userEvent.click(within(shop).getByRole("button", { name: "Save" }));

    expect(await within(shop).findByRole("alert")).toHaveTextContent(
      "URL: invalid url: must be http:// or https:// with a host and a valid port",
    );
    expect(url).toHaveValue("ftp://shop");
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

  it("shows the hosts file's protected containers as fixed and only the dashboard's as removable", async () => {
    const saved = settingsView();
    saved.overrides.hosts = { m1: { services: {} } };
    saved.hosts[0].services["compose:shop"].protected_containers = ["shop-db-1"];
    const fetchMock = backend([json(saved)]);
    renderPage();
    const shop = await shopSection();

    const fixed = within(shop).getByRole("list", { name: "Protected by the hosts file: shop" });
    expect(within(fixed).getByText("shop-db-1")).toBeInTheDocument();
    expect(within(shop).queryByRole("button", { name: "Remove shop-db-1" })).toBeNull();

    await userEvent.click(within(shop).getByRole("button", { name: "Remove shop-cache-1" }));
    await userEvent.click(within(shop).getByRole("button", { name: "Save" }));
    expect(sentBody(fetchMock, "PUT")).toEqual({ hosts: { m1: { services: {} } } });
    expect(await within(shop).findByText("None added here.")).toBeInTheDocument();
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

  it("after a 409 reads the settings again, says the save was not applied and never retries it", async () => {
    // Elsewhere, shop-web-1 was protected after this page loaded; the stale save would drop it.
    const now = settingsView({ version: "v2" });
    now.overrides.hosts = {
      m1: {
        services: { "compose:shop": { protected_containers: ["shop-cache-1", "shop-web-1"] } },
      },
    };
    now.hosts[0].services["compose:shop"].protected_containers = ["shop-cache-1", "shop-web-1"];
    const fetchMock = stubBackend({
      "GET /api/settings": [json(settingsView()), json(now)],
      "GET /api/services": json([SHOP, WORKER]),
      "PUT /api/settings": [json({ detail: "the settings changed", version: "v2" }, 409)],
    });
    renderPage();
    const shop = await shopSection();

    await userEvent.click(within(shop).getByRole("button", { name: "Remove shop-cache-1" }));
    await userEvent.click(within(shop).getByRole("button", { name: "Save" }));

    expect((await screen.findAllByRole("alert"))[0]).toHaveTextContent(SETTINGS_CHANGED);
    expect(await within(shop).findByDisplayValue("shop-web-1")).toBeInTheDocument();
    expect(within(shop).getByDisplayValue("shop-cache-1")).toBeInTheDocument();
    const puts = fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT");
    expect(puts).toHaveLength(1);
    expect(fetchMock.mock.calls.filter(([path]) => path === "/api/settings")).toHaveLength(3);
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
    const fetchMock = backend([
      new Promise<Response>((resolve) => (answer = resolve)) as unknown as Response,
      json(saved),
    ]);
    renderPage();
    const shop = await shopSection();

    await userEvent.type(
      within(shop).getByPlaceholderText("Container name, such as shop-db-1"),
      "shop-web-1{Enter}",
    );
    await userEvent.click(within(shop).getByRole("button", { name: "Save" }));
    await userEvent.type(
      within(shop).getByRole("textbox", { name: "Rules for shop: entry 1" }),
      "!",
    );
    const saves = within(shop).getAllByRole("button", { name: "Save" });
    expect(saves).toHaveLength(2);
    for (const button of saves) expect(button).toBeDisabled();

    answer(json(saved));
    await waitFor(() => expect(within(shop).getByRole("button", { name: "Save" })).toBeEnabled());
    await userEvent.click(within(shop).getByRole("button", { name: "Save" }));
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

  it("lists a service Bleep sees but has no settings for", async () => {
    backend();
    renderPage();
    const worker = await screen.findByRole("region", { name: "Service worker" });
    expect(within(worker).getByText("No URLs are checked.")).toBeInTheDocument();
  });
});
