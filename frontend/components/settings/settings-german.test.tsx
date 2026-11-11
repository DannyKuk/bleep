import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { HostForm, RemoveHost } from "@/components/settings/host-form";
import {
  json,
  plugin,
  secretViews,
  settingsView,
  sshHost,
  stubBackend,
  telegram,
} from "@/components/settings/fixtures";
import { problemsOf } from "@/components/settings/save-error";
import { SettingsPage } from "@/components/settings/settings-page";
import { ApiError } from "@/lib/api";
import { MESSAGES, setLanguage } from "@/lib/i18n";

function backend(view = settingsView(), plugins = [plugin()]) {
  return stubBackend({
    "GET /api/settings": json(view),
    "GET /api/plugins": json(plugins),
    "GET /api/telegram/status": json(telegram()),
    "GET /api/secrets": json(secretViews()),
  });
}

describe("the settings page in German", () => {
  it("shows its headings, panels and forms in German", async () => {
    backend(settingsView({ language: "de" }));
    await act(async () => setLanguage("de"));
    render(<SettingsPage />);

    expect(await screen.findByRole("heading", { name: "Einstellungen" })).toBeVisible();
    await screen.findByRole("button", { name: "SSH-Host hinzufügen" });
    await screen.findByRole("group", { name: "OPENROUTER_API_KEY" });
    await screen.findByText("Letzte Abfrage");
    for (const name of [
      "Hosts",
      "Zeitzone",
      "Sprache",
      "Schwellenwerte",
      "Modelle",
      "Embeddings",
      "Tracing",
      "Plugins",
      "Zugangsdaten",
      "Telegram",
    ]) {
      expect(screen.getByRole("region", { name })).toBeVisible();
    }
    const thresholds = screen.getByRole("region", { name: "Schwellenwerte" });
    expect(within(thresholds).getByText("Festplatte belegt ab")).toBeVisible();
    expect(
      within(thresholds).getByRole("button", { name: "Schwellenwerte speichern" }),
    ).toBeDisabled();
    const models = screen.getByRole("region", { name: "Modelle" });
    expect(within(models).getByLabelText("Anbieter")).toBeVisible();
    expect(screen.getByRole("button", { name: "SSH-Host hinzufügen" })).toBeVisible();
  });

  it("writes the plugin states, the Telegram facts and the stored secrets in German", async () => {
    backend(settingsView({ language: "de" }));
    await act(async () => setLanguage("de"));
    render(<SettingsPage />);

    const plugins = await screen.findByRole("region", { name: "Plugins" });
    expect(await within(plugins).findByText("Aus")).toBeVisible();
    await screen.findByText("Letzte Abfrage");
    expect(within(plugins).getByRole("button", { name: "Einschalten" })).toBeVisible();
    const telegramPanel = screen.getByRole("region", { name: "Telegram" });
    expect(within(telegramPanel).getByText("Eingerichtet")).toBeVisible();
    expect(within(telegramPanel).getByText("Letzte Abfrage")).toBeVisible();
    expect(
      within(screen.getByRole("group", { name: "OPENROUTER_API_KEY" })).getByText(
        "In der .env gesetzt",
      ),
    ).toBeVisible();
  });

  it("says in German that the settings changed elsewhere", async () => {
    const stale = Response.json({ detail: "stale" }, { status: 409 });
    stubBackend({
      "GET /api/settings": [
        json(settingsView({ language: "de" })),
        json(settingsView({ language: "de" })),
      ],
      "GET /api/plugins": json([plugin()]),
      "GET /api/telegram/status": json(telegram()),
      "GET /api/secrets": json(secretViews()),
      "PUT /api/settings": stale,
    });
    await act(async () => setLanguage("de"));
    render(<SettingsPage />);
    const thresholds = await screen.findByRole("region", { name: "Schwellenwerte" });
    await userEvent.clear(within(thresholds).getAllByRole("textbox")[0]);
    await userEvent.type(within(thresholds).getAllByRole("textbox")[0], "9");
    await userEvent.click(
      within(thresholds).getByRole("button", { name: "Schwellenwerte speichern" }),
    );

    expect((await screen.findAllByRole("alert"))[0]).toHaveTextContent(
      MESSAGES.de.settings.changedElsewhere,
    );
  });
});

describe("long German labels on the settings page", () => {
  it("wrap inside their buttons instead of overflowing a phone's width", async () => {
    backend(settingsView({ language: "de", restart_needed: [] }));
    await act(async () => setLanguage("de"));
    render(<SettingsPage />);

    const embeddings = await screen.findByRole("region", { name: "Embeddings" });
    await userEvent.selectOptions(within(embeddings).getByLabelText("Embedder"), "openai");
    await userEvent.click(within(embeddings).getByRole("button", { name: "Embeddings speichern" }));
    const confirm = await within(embeddings).findByRole("button", {
      name: "Für den nächsten Start speichern",
    });
    for (const button of [
      confirm,
      screen.getByRole("button", { name: "SSH-Host hinzufügen" }),
      within(screen.getByRole("region", { name: "Schwellenwerte" })).getByRole("button", {
        name: "Schwellenwerte speichern",
      }),
    ]) {
      expect(button).toHaveClass("whitespace-normal", "h-auto", "min-h-9");
    }
  });
});

describe("the host form in German", () => {
  it("labels its fields and the host key check in German", async () => {
    stubBackend({});
    await act(async () => setLanguage("de"));
    render(<HostForm onChanged={async () => undefined} />);

    const form = screen.getByRole("form", { name: "Host hinzufügen" });
    for (const label of ["Name", "ID", "Adresse", "Port", "Benutzer", "Docker-Socket"]) {
      expect(within(form).getByLabelText(label)).toBeVisible();
    }
    expect(within(form).getByLabelText("Privater SSH-Schlüssel")).toBeVisible();
    expect(within(form).getByRole("button", { name: "Hostschlüssel prüfen" })).toBeVisible();
    expect(
      within(form).getByText("Braucht den Schlüssel und einen bestätigten Hostschlüssel."),
    ).toBeVisible();
    expect(within(form).getByText("bleep_key.pub").tagName).toBe("CODE");
  });

  it("asks before removing a host, in German", async () => {
    stubBackend({});
    await act(async () => setLanguage("de"));
    const host = sshHost({ id: "box", name: "Box" });
    host.connection = { ...host.connection, source: "dashboard", version: "v1" };
    render(<RemoveHost host={host} onChanged={async () => undefined} />);

    await userEvent.click(screen.getByRole("button", { name: "Entfernen" }));
    expect(screen.getByText(/Bleep hört auf, Box zu beobachten/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Ja, Host entfernen" })).toBeVisible();
  });
});

describe("problemsOf with the German field labels", () => {
  it("names the field in German and keeps the API's own reason", () => {
    const error = new ApiError(422, "refused", undefined, {
      detail: [{ loc: ["body", "port"], msg: "Input should be at most 65535" }, { loc: ["x"] }],
    });
    expect(
      problemsOf(error, MESSAGES.de.settings.hostForm.labels, MESSAGES.de.settings.invalid),
    ).toEqual(["Port: Input should be at most 65535", "ungültig"]);
  });
});
