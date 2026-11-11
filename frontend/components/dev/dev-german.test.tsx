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
  incidents: [],
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
    expect(screen.getByText("Noch kein Vorfall hat ein Modell aufgerufen.")).toBeVisible();
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
