import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LogoutButton } from "@/components/logout-button";
import { PreferencesProvider } from "@/components/preferences";
import { settingsView } from "@/components/settings/fixtures";

afterEach(() => vi.unstubAllGlobals());

function settings(view = settingsView()) {
  vi.stubGlobal("fetch", async () => Response.json(view));
}

describe("LogoutButton", () => {
  it("is there while Bleep's login is on", async () => {
    settings();
    render(
      <PreferencesProvider>
        <LogoutButton />
      </PreferencesProvider>,
    );
    expect(await screen.findByRole("button", { name: /Log out/ })).toBeInTheDocument();
  });

  it("goes away once the settings say the login is off: there is nothing to log out of", async () => {
    settings(settingsView({ login_enabled: false }));
    render(
      <PreferencesProvider>
        <LogoutButton />
      </PreferencesProvider>,
    );
    await vi.waitFor(() => expect(screen.queryByRole("button", { name: /Log out/ })).toBeNull());
  });

  it("never shows while the server layout says the login is off, not even before the settings are read (#626)", async () => {
    vi.stubGlobal("fetch", () => new Promise(() => {})); // the settings never arrive
    render(
      <PreferencesProvider loginEnabled={false}>
        <LogoutButton />
      </PreferencesProvider>,
    );
    await Promise.resolve();
    expect(screen.queryByRole("button", { name: /Log out/ })).toBeNull();
  });
});
