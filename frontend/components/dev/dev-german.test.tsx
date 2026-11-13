import { act, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DevPanelLink } from "@/components/dev-panel-link";
import { CostTables } from "@/components/dev/costs";
import { DevPanel } from "@/components/dev/dev-panel";
import { json, prompt, settingsView, stubBackend } from "@/components/dev/fixtures";
import { GuardEntries } from "@/components/dev/guard-log";
import { PromptEditor } from "@/components/dev/prompts";
import type { CostView } from "@/lib/api-types";
import { setLanguage } from "@/lib/i18n";

const COSTS: CostView = {
  daily_cap_usd: 1,
  spent_today_usd: 0.375,
  unpriced_today: 2,
  days: [
    { day: "2026-11-03", calls: 1234, tokens_in: 15000, tokens_out: 1500, usd: 0.375, unpriced: 1 },
  ],
  models: [
    {
      model: "openai/gpt-6-luna",
      calls: 1234,
      tokens_in: 15000,
      tokens_out: 1500,
      usd: 0.375,
      unpriced: 0,
    },
  ],
  models_today: [],
  incidents: [
    {
      incident_id: "inc_shop",
      title: "Container gestorben",
      status: "open",
      opened_at: "2026-11-03T10:00:00Z",
      trace_url: null,
      prompt_overrides: {},
      models: ["claude-sonnet-5-5"],
      calls: 1,
      tokens_in: 1,
      tokens_out: 1,
      usd: 0,
      unpriced: 0,
    },
  ],
};

describe("the developer panel in German", () => {
  it("says how to turn the panel on in German", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async () => json({ detail: "Not Found" }, 404)),
    );
    await act(async () => setLanguage("de"));
    render(<DevPanel />);

    expect(await screen.findByRole("heading", { name: "Entwickler" })).toBeVisible();
    expect(await screen.findByText(/Der Entwicklerbereich ist aus/)).toHaveTextContent(
      "BLEEP_DEV_PANEL=true",
    );
  });

  it("shows its four panels in German", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async (input) => {
        const path = String(input);
        if (path === "/api/dev") return json({ enabled: true });
        if (path === "/api/settings") return json(settingsView());
        if (path === "/api/dev/prompts") return json([prompt()]);
        if (path.startsWith("/api/dev/costs")) {
          return json({ ...COSTS, days: [], unpriced_today: 0 });
        }
        return json([]);
      }),
    );
    await act(async () => setLanguage("de"));
    render(<DevPanel />);

    for (const name of ["Budgets", "Prompts", "Kosten", "Schutzprotokoll"]) {
      expect(await screen.findByRole("region", { name })).toBeVisible();
    }
    const budgets = screen.getByRole("region", { name: "Budgets" });
    expect(await within(budgets).findByLabelText("Tokens pro Vorfall")).toBeVisible();
    expect(within(budgets).getByRole("button", { name: "Budgets speichern" })).toBeDisabled();
    expect(await screen.findByRole("textbox", { name: /Text von/ })).toBeVisible();
  });

  it("writes dollars and counts the German way, and the unpriced calls in German", async () => {
    await act(async () => setLanguage("de"));
    render(<CostTables costs={COSTS} />);

    expect(
      screen.getByText(
        "Heute: $0,38 vom Tageslimit von $1,00. 2 Aufrufe heute haben keinen bekannten Preis und sind nicht in dieser Summe.",
      ),
    ).toBeVisible();
    const day = within(screen.getByRole("table", { name: "Kosten pro Tag" }));
    expect(day.getByText("1.234")).toBeVisible();
    expect(day.getByText("15.000")).toBeVisible();
    expect(day.getByText("$0,38 + 1 ohne Preis")).toBeVisible();
    expect(screen.getByText(/Die Dollar-Beträge summieren nur/)).toBeVisible();
    const models = within(screen.getByRole("table", { name: "Kosten pro Modell" }));
    expect(models.getByText("openai/gpt-6-luna")).toBeVisible();
    expect(models.getByText("Heute")).toBeVisible();
    expect(models.getByText("keine")).toBeVisible();
    const incidents = within(screen.getByRole("table", { name: "Kosten pro Vorfall" }));
    expect(incidents.getByText("Modelle")).toBeVisible();
    expect(incidents.getByText("claude-sonnet-5-5")).toBeVisible();
  });

  it("names a guard entry's kind and time in German", async () => {
    await act(async () => setLanguage("de"));
    render(
      <GuardEntries
        entries={[
          {
            id: 1,
            incident_id: "inc_1",
            kind: "refused_command",
            detail: "rm -rf /",
            at: "2026-11-03T10:00:00Z",
          },
        ]}
        onIncident={() => undefined}
      />,
    );

    const list = screen.getByRole("list", { name: "Schutzprotokoll" });
    expect(list).toHaveTextContent("Abgelehnter Befehl");
    expect(list).toHaveTextContent("03.11.2026");
  });

  it("describes a prompt override in German", async () => {
    stubBackend(json(prompt()));
    await act(async () => setLanguage("de"));
    render(
      <PromptEditor prompt={prompt()} onSaved={() => undefined} onStale={async () => undefined} />,
    );

    expect(screen.getByRole("button", { name: "Überschreibung speichern" })).toBeDisabled();
    expect(screen.getByText(/Nutze genau die Platzhalter der Datei/)).toBeVisible();
  });

  it("does not read again, and translates a failed read, when the language changes", async () => {
    const fetchMock = vi.fn<typeof fetch>(async (input) => {
      const path = String(input);
      if (path === "/api/dev") return json({ enabled: true });
      if (path === "/api/dev/prompts") return json({ detail: "boom" }, 500);
      if (path === "/api/settings") return json({ detail: "boom" }, 500);
      if (path.startsWith("/api/dev/costs")) return json({ ...COSTS, days: [], unpriced_today: 0 });
      return json([]);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<DevPanel />);
    expect(await screen.findByText("Could not load the prompts: boom")).toBeVisible();
    const calls = fetchMock.mock.calls.length;

    await act(async () => setLanguage("de"));

    expect(await screen.findByText("Die Prompts konnten nicht geladen werden: boom")).toBeVisible();
    expect(screen.getByText("Die Einstellungen konnten nicht geladen werden: boom")).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(calls);
  });

  it("lets the buttons of the panel wrap, so a long German label stays inside a phone's width", async () => {
    stubBackend(json(prompt()));
    await act(async () => setLanguage("de"));
    render(
      <PromptEditor
        prompt={prompt({ override: "x" })}
        onSaved={() => undefined}
        onStale={async () => undefined}
      />,
    );
    for (const name of ["Überschreibung speichern", "Auf die Datei zurücksetzen"]) {
      expect(screen.getByRole("button", { name })).toHaveClass(
        "whitespace-normal",
        "h-auto",
        "min-h-11",
      );
    }
  });

  it("links to the developer panel in German", async () => {
    stubBackend(json({ enabled: true }));
    await act(async () => setLanguage("de"));
    render(<DevPanelLink />);

    expect(await screen.findByRole("link", { name: "Entwicklerbereich" })).toHaveAttribute(
      "href",
      "/dev",
    );
  });
});
