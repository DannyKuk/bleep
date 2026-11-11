import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import {
  invalid,
  json,
  plugin,
  secretViews,
  sentBody,
  settingsView,
  stubBackend,
  telegram,
} from "@/components/settings/fixtures";
import { SettingsPage } from "@/components/settings/settings-page";
import { zoneChoices } from "@/components/settings/time-zone-form";
import { PreferencesProvider } from "@/components/preferences";
import { formatDateTime } from "@/lib/format";
import type { SettingsView } from "@/lib/settings-types";
import { browserZone } from "@/lib/zone";

const LAST_POLL = "2026-11-03T10:00:00Z";
const NEW_YORK = "America/New_York";

function backend(view: SettingsView, put: Response[] = []) {
  return stubBackend({
    "GET /api/settings": json(view),
    "GET /api/plugins": json([plugin()]),
    "GET /api/telegram/status": json(telegram({ last_poll: LAST_POLL })),
    "GET /api/secrets": json(secretViews()),
    "PUT /api/settings": put,
  });
}

/** The settings page inside the app's time zone, as the app layout has it. */
function renderPage() {
  const page = render(
    <PreferencesProvider>
      <SettingsPage />
    </PreferencesProvider>,
  );
  const lastPoll = () => page.container.querySelector(`time[datetime="${LAST_POLL}"]`);
  return { lastPoll };
}

const panel = () => screen.findByRole("region", { name: "Time zone" });

describe("the time zone on the settings page", () => {
  it("says Telegram shows UTC until a zone is chosen, and offers the browser's zone first", async () => {
    backend(settingsView({ timezone: null }));
    renderPage();
    const zone = await panel();
    expect(zone).toHaveTextContent("Telegram shows UTC");
    const select = within(zone).getByRole("combobox", { name: "Show every time in" });
    expect(select).toHaveValue(browserZone());
    expect(within(select).getAllByRole("option")[0]).toHaveTextContent(
      `${browserZone()} (this browser)`,
    );
  });

  it("saves the chosen zone, and every time on the dashboard follows it", async () => {
    const chosen = settingsView({
      timezone: NEW_YORK,
      overrides: { general: { timezone: NEW_YORK } },
      version: "v2",
    });
    const fetchMock = backend(settingsView({ timezone: null }), [json(chosen)]);
    const { lastPoll } = renderPage();
    const zone = await panel();
    await waitFor(() =>
      expect(lastPoll()).toHaveTextContent(formatDateTime(LAST_POLL, undefined, "en")),
    );

    await userEvent.selectOptions(within(zone).getByRole("combobox"), NEW_YORK);
    await userEvent.click(within(zone).getByRole("button", { name: "Save time zone" }));

    expect(await within(zone).findByText(/show every time in America\/New_York/)).toBeVisible();
    expect(sentBody(fetchMock, "PUT")).toEqual({ general: { timezone: NEW_YORK } });
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
    expect(put?.[1]?.headers).toMatchObject({ "if-match": '"v1"' });
    await waitFor(() =>
      expect(lastPoll()).toHaveTextContent(formatDateTime(LAST_POLL, NEW_YORK, "en")),
    );
  });

  it("shows why a zone was refused and keeps the one in force", async () => {
    backend(settingsView({ timezone: null }), [
      invalid(
        ["general", "timezone"],
        "Value error, must be an IANA time zone, e.g. Europe/Vienna",
      ),
    ]);
    renderPage();
    const zone = await panel();
    await userEvent.selectOptions(within(zone).getByRole("combobox"), NEW_YORK);
    await userEvent.click(within(zone).getByRole("button", { name: "Save time zone" }));

    expect(await within(zone).findByRole("alert")).toHaveTextContent(
      "Time zone: must be an IANA time zone, e.g. Europe/Vienna",
    );
    expect(zone).toHaveTextContent("Telegram shows UTC");
  });

  it("says a zone from .env is the starting value, and a chosen one can be cleared", async () => {
    const fromEnv = settingsView({ timezone: "Europe/Vienna" });
    backend(fromEnv);
    renderPage();
    const zone = await panel();
    expect(zone).toHaveTextContent("Europe/Vienna comes from BLEEP_TIMEZONE");
    expect(within(zone).queryByRole("button", { name: "Clear the choice" })).toBeNull();
  });

  it("clears a chosen zone with an empty section", async () => {
    const chosen = settingsView({
      timezone: NEW_YORK,
      overrides: { general: { timezone: NEW_YORK } },
    });
    const fetchMock = backend(chosen, [json(settingsView({ timezone: null }))]);
    renderPage();
    const zone = await panel();
    await userEvent.click(within(zone).getByRole("button", { name: "Clear the choice" }));

    expect(await within(zone).findByText(/Telegram shows UTC/)).toBeVisible();
    expect(sentBody(fetchMock, "PUT")).toEqual({ general: {} });
  });
});

describe("zoneChoices", () => {
  it("lists the browser's zone first, a stored one the browser does not list, and UTC once", () => {
    const choices = zoneChoices("Europe/Vienna", "Asia/Calcutta");
    expect(choices.slice(0, 3)).toEqual(["Europe/Vienna", "Asia/Calcutta", "UTC"]);
    expect(choices.filter((zone) => zone === "UTC")).toHaveLength(1);
    expect(choices.filter((zone) => zone === "Europe/Vienna")).toHaveLength(1);
    expect(choices).toContain(NEW_YORK);
  });
});
