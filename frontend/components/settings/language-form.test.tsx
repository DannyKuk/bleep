import { render, screen, within } from "@testing-library/react";
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
import { TimeZoneProvider } from "@/components/time-zone";
import type { SettingsView } from "@/lib/settings-types";

const VIENNA = "Europe/Vienna";

function backend(view: SettingsView, put: Response[] = []) {
  return stubBackend({
    "GET /api/settings": json(view),
    "GET /api/plugins": json([plugin()]),
    "GET /api/telegram/status": json(telegram()),
    "GET /api/secrets": json(secretViews()),
    "PUT /api/settings": put,
  });
}

function renderPage() {
  render(
    <TimeZoneProvider>
      <SettingsPage />
    </TimeZoneProvider>,
  );
}

const panel = () => screen.findByRole("region", { name: "Language" });

describe("the language on the settings page", () => {
  it("shows English until another is chosen, and says names and steps stay as they are", async () => {
    backend(settingsView());
    renderPage();
    const language = await panel();
    const select = within(language).getByRole("combobox", { name: "Bleep writes in" });
    expect(select).toHaveValue("en");
    expect(
      within(select)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["English", "Deutsch (German)"]);
    expect(language).toHaveTextContent("stay exactly as the host names them");
    expect(within(language).getByRole("button", { name: "Save language" })).toBeDisabled();
  });

  it("saves German and sends the chosen time zone along, since the section is replaced whole", async () => {
    const zoned = settingsView({
      timezone: VIENNA,
      overrides: { general: { timezone: VIENNA } },
    });
    const german = settingsView({
      timezone: VIENNA,
      language: "de",
      overrides: { general: { timezone: VIENNA, language: "de" } },
      version: "v2",
    });
    const fetchMock = backend(zoned, [json(german)]);
    renderPage();
    const language = await panel();

    await userEvent.selectOptions(within(language).getByRole("combobox"), "de");
    await userEvent.click(within(language).getByRole("button", { name: "Save language" }));

    expect(await within(language).findByText(/recovery lines in German/)).toBeVisible();
    expect(within(language).getByText(/Telegram messages, buttons and chat answers/)).toBeVisible();
    expect(sentBody(fetchMock, "PUT")).toEqual({ general: { timezone: VIENNA, language: "de" } });
  });

  it("keeps German when the time zone is cleared", async () => {
    const both = settingsView({
      timezone: VIENNA,
      language: "de",
      overrides: { general: { timezone: VIENNA, language: "de" } },
    });
    const fetchMock = backend(both, [
      json(settingsView({ language: "de", overrides: { general: { language: "de" } } })),
    ]);
    renderPage();
    const zone = await screen.findByRole("region", { name: "Time zone" });
    await userEvent.click(within(zone).getByRole("button", { name: "Clear the choice" }));

    expect(await within(zone).findByText(/Telegram shows UTC/)).toBeVisible();
    expect(sentBody(fetchMock, "PUT")).toEqual({ general: { language: "de" } });
  });

  it("shows why a language was refused", async () => {
    backend(settingsView(), [invalid(["general", "language"], "Input should be 'en' or 'de'")]);
    renderPage();
    const language = await panel();
    await userEvent.selectOptions(within(language).getByRole("combobox"), "de");
    await userEvent.click(within(language).getByRole("button", { name: "Save language" }));

    expect(await within(language).findByRole("alert")).toHaveTextContent(
      "Language: Input should be 'en' or 'de'",
    );
  });
});
