import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import {
  json,
  plugin,
  secretViews,
  settingsView,
  sshHost,
  stubBackend,
  telegram,
} from "@/components/settings/fixtures";
import { SettingsPage } from "@/components/settings/settings-page";
import { SETTINGS_CHANGED } from "@/lib/settings-api";

function backend(put: Response[] = [], view = settingsView()) {
  return stubBackend({
    "GET /api/settings": json(view),
    "GET /api/plugins": json([plugin()]),
    "GET /api/telegram/status": json(telegram()),
    "GET /api/secrets": json(secretViews()),
    "PUT /api/settings": put,
  });
}

describe("SettingsPage", () => {
  it("lists the hosts as links to their pages, with no host settings of their own", async () => {
    const box = sshHost({ id: "box", name: "Box", services: {} });
    box.connection = { ...box.connection, source: "dashboard" };
    backend([], settingsView({ hosts: [sshHost(), box] }));
    render(<SettingsPage />);

    const hosts = await screen.findByRole("list", { name: "Hosts" });
    expect(within(hosts).getByRole("link", { name: /M1/ })).toHaveAttribute("href", "/hosts/m1");
    expect(within(hosts).getByRole("link", { name: /M1/ })).toHaveTextContent(
      "from config/hosts.yaml",
    );
    expect(within(hosts).getByRole("link", { name: /Box/ })).toHaveTextContent(
      "added on the dashboard",
    );
    expect(screen.queryByRole("region", { name: "Service shop" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Connection" })).toBeNull();
    expect(screen.getByRole("button", { name: "Add an SSH host" })).toBeInTheDocument();
  });

  it("shows a key set in .env as set there", async () => {
    backend();
    render(<SettingsPage />);
    const key = await screen.findByRole("group", { name: "OPENROUTER_API_KEY" });
    expect(key).toHaveTextContent("Set in .env");
    expect(screen.getByText("env TELEGRAM_BOT_TOKEN").closest("span")).toHaveTextContent(
      "Set: env TELEGRAM_BOT_TOKEN",
    );
  });

  it("says socket and loopback hosts go in the hosts file", async () => {
    backend();
    render(<SettingsPage />);
    const hosts = await screen.findByRole("region", { name: "Hosts" });
    expect(hosts).toHaveTextContent("config/hosts.yaml");
    expect(hosts).toHaveTextContent("loopback");
  });

  it("keeps both of two plugin saves", async () => {
    const mcp = plugin({ name: "mcp", title: "MCP", settings_schema: {} });
    stubBackend({
      "GET /api/settings": json(settingsView()),
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

  it("sends a plugin's own version, and after a 409 reads the plugins again without retrying", async () => {
    // Elsewhere the plugin was pointed at another instance after this page loaded.
    const moved = plugin({ settings: { deploy_timeout_s: 1800 }, version: "p2" });
    const fetchMock = stubBackend({
      "GET /api/settings": json(settingsView()),
      "GET /api/plugins": [json([plugin()]), json([moved])],
      "GET /api/telegram/status": json(telegram()),
      "PUT /api/plugins/coolify": [json({ detail: "the plugin changed", version: "p2" }, 409)],
    });
    render(<SettingsPage />);
    const coolify = await screen.findByRole("region", { name: "Plugin Coolify" });
    await userEvent.type(
      within(coolify).getByRole("textbox", { name: "Deploy Timeout S" }),
      "1200",
    );
    await userEvent.click(within(coolify).getByRole("button", { name: "Save settings" }));

    expect((await screen.findAllByRole("alert"))[0]).toHaveTextContent(SETTINGS_CHANGED);
    expect(await screen.findByDisplayValue("1800")).toBeInTheDocument();
    const puts = fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT");
    expect(puts).toHaveLength(1);
    expect(puts[0][1]?.headers).toMatchObject({ "if-match": '"p1"' });
  });

  it("says when a section cannot be loaded", async () => {
    stubBackend({
      "GET /api/settings": json({ detail: "boom" }, 500),
      "GET /api/plugins": json([]),
      "GET /api/telegram/status": json(telegram()),
      "GET /api/secrets": json(secretViews()),
    });
    render(<SettingsPage />);
    expect((await screen.findAllByRole("alert"))[0]).toHaveTextContent(
      "Could not load the settings: boom",
    );
    expect(await screen.findByText("This Bleep has no plugins.")).toBeInTheDocument();
  });
});
