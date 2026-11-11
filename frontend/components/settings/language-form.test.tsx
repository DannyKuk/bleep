import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AppNav } from "@/components/app-nav";
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
import { PreferencesProvider } from "@/components/preferences";
import type { SettingsView } from "@/lib/settings-types";

vi.mock("next/navigation", () => ({ usePathname: () => "/settings" }));

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
    <PreferencesProvider>
      <SettingsPage />
    </PreferencesProvider>,
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
    expect(language).toHaveTextContent("This dashboard follows it too");
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

    expect(await within(language).findByText(/Zeilen zur Behebung auf Deutsch/)).toBeVisible();
    expect(
      within(language).getByText(/Telegram-Nachrichten, Buttons und Chat-Antworten/),
    ).toBeVisible();
    expect(sentBody(fetchMock, "PUT")).toEqual({ general: { timezone: VIENNA, language: "de" } });
  });

  it("turns the rest of the dashboard German and the page's language to de once German is saved", async () => {
    const german = settingsView({
      language: "de",
      overrides: { general: { language: "de" } },
      version: "v2",
    });
    backend(settingsView(), [json(german)]);
    render(
      <PreferencesProvider>
        <AppNav />
        <SettingsPage />
      </PreferencesProvider>,
    );
    expect(screen.getByRole("link", { name: "Incidents" })).toBeVisible();
    expect(document.documentElement.lang).toBe("en");
    const language = await panel();

    await userEvent.selectOptions(within(language).getByRole("combobox"), "de");
    await userEvent.click(within(language).getByRole("button", { name: "Save language" }));

    expect(await screen.findByRole("link", { name: "Vorfälle" })).toBeVisible();
    expect(screen.getByRole("navigation", { name: "Hauptmenü" })).toBeVisible();
    expect(document.documentElement.lang).toBe("de");
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
    const zone = await screen.findByRole("region", { name: "Zeitzone" });
    await userEvent.click(within(zone).getByRole("button", { name: "Auswahl löschen" }));

    expect(await within(zone).findByText(/Telegram zeigt UTC/)).toBeVisible();
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
