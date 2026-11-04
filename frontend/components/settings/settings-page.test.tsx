import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import {
  invalid,
  json,
  plugin,
  sentBody,
  settingsView,
  stubBackend,
  telegram,
} from "@/components/settings/fixtures";
import { SettingsPage } from "@/components/settings/settings-page";
import type { ServiceView } from "@/lib/api-types";
import { SETTINGS_CHANGED } from "@/lib/settings-api";

const SHOP: ServiceView = {
  id: "m1/compose:shop",
  host_id: "m1",
  name: "shop",
  source: "compose",
  health: "ok",
  containers: [],
  urls: [],
  open_incident_id: null,
};
const WORKER: ServiceView = { ...SHOP, id: "m1/compose:worker", name: "worker" };

function backend(put: Response[] = [], view = settingsView()) {
  return stubBackend({
    "GET /api/settings": json(view),
    "GET /api/services": json([SHOP, WORKER]),
    "GET /api/plugins": json([plugin()]),
    "GET /api/telegram/status": json(telegram()),
    "PUT /api/settings": put,
  });
}

async function shopSection() {
  return screen.findByRole("region", { name: "Service shop" });
}

describe("SettingsPage", () => {
  it("shows the host's connection read-only and every secret as set or missing, with no input for one", async () => {
    backend();
    render(<SettingsPage />);

    const host = await screen.findByRole("region", { name: "Host M1" });
    expect(within(host).getByText("From the hosts file; change it there")).toBeInTheDocument();
    expect(within(host).getByText("danny@m1.local:22")).toBeInTheDocument();
    expect(within(host).getByText("Missing:")).toBeInTheDocument();
    expect(within(host).getByText("file /keys/id_ed25519")).toBeInTheDocument();
    expect(within(host).queryByDisplayValue("m1.local")).not.toBeInTheDocument();

    expect(await screen.findByText("env OPENROUTER_API_KEY")).toBeInTheDocument();
    expect(screen.getByText("env TELEGRAM_BOT_TOKEN")).toBeInTheDocument();
    expect(screen.getByText("env COOLIFY_TOKEN")).toBeInTheDocument();
    for (const input of screen.getAllByRole("textbox")) {
      expect(input).not.toHaveAccessibleName(/token|key|password|secret/i);
    }
    expect(document.querySelector('input[type="password"]')).toBeNull();
  });

  it("lists a service Bleep sees but has no settings for", async () => {
    backend();
    render(<SettingsPage />);
    const worker = await screen.findByRole("region", { name: "Service worker" });
    expect(within(worker).getByText("No URLs are checked.")).toBeInTheDocument();
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
    render(<SettingsPage />);
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
    render(<SettingsPage />);
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
    render(<SettingsPage />);
    const host = await screen.findByRole("region", { name: "Host M1" });

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
    render(<SettingsPage />);
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
    render(<SettingsPage />);
    const host = await screen.findByRole("region", { name: "Host M1" });
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
      "GET /api/plugins": json([plugin()]),
      "GET /api/telegram/status": json(telegram()),
      "PUT /api/settings": [json({ detail: "the settings changed", version: "v2" }, 409)],
    });
    render(<SettingsPage />);
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
    render(<SettingsPage />);
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

  it("keeps both of two plugin saves", async () => {
    const mcp = plugin({ name: "mcp", title: "MCP", settings_schema: {} });
    stubBackend({
      "GET /api/settings": json(settingsView()),
      "GET /api/services": json([SHOP]),
      "GET /api/plugins": json([plugin(), mcp]),
      "GET /api/telegram/status": json(telegram()),
      "PUT /api/plugins/coolify": json(plugin({ enabled: true })),
      "PUT /api/plugins/mcp": json({ ...mcp, enabled: true }),
    });
    render(<SettingsPage />);
    const coolify = await screen.findByRole("region", { name: "Plugin Coolify" });
    const other = screen.getByRole("region", { name: "Plugin MCP" });
    await userEvent.click(within(coolify).getByRole("button", { name: "Switch on" }));
    await userEvent.click(await within(other).findByRole("button", { name: "Switch on" }));

    expect(await within(other).findByRole("button", { name: "Switch off" })).toBeInTheDocument();
    expect(within(coolify).getByRole("button", { name: "Switch off" })).toBeInTheDocument();
  });

  it("says when the services Bleep sees cannot be loaded", async () => {
    stubBackend({
      "GET /api/settings": json(settingsView()),
      "GET /api/services": json({ detail: "the registry is down" }, 503),
      "GET /api/plugins": json([]),
      "GET /api/telegram/status": json(telegram()),
    });
    render(<SettingsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load the services Bleep sees: the registry is down",
    );
    expect(await screen.findByRole("region", { name: "Service compose:shop" })).toBeInTheDocument();
  });

  it("says when a section cannot be loaded", async () => {
    stubBackend({
      "GET /api/settings": json({ detail: "boom" }, 500),
      "GET /api/services": json([]),
      "GET /api/plugins": json([]),
      "GET /api/telegram/status": json(telegram()),
    });
    render(<SettingsPage />);
    expect((await screen.findAllByRole("alert"))[0]).toHaveTextContent(
      "Could not load the settings: boom",
    );
    expect(await screen.findByText("This Bleep has no plugins.")).toBeInTheDocument();
  });
});
