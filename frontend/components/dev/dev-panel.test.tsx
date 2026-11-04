import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DevPanel } from "@/components/dev/dev-panel";
import { BUDGETS, json, prompt, settingsView } from "@/components/dev/fixtures";

function stubRoutes(dev: Response) {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const path = String(input);
    if (path === "/api/dev") return dev;
    if (path === "/api/settings") return json(settingsView());
    if (path === "/api/dev/prompts") return json([prompt()]);
    if (path.startsWith("/api/dev/costs"))
      return json({
        incidents: [],
        days: [],
        daily_cap_usd: 1,
        spent_today_usd: 0,
        unpriced_today: 0,
      });
    return json([]);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("DevPanel", () => {
  it("says how to turn the panel on when the backend has it off", async () => {
    const fetchMock = stubRoutes(json({ detail: "Not Found" }, 404));
    render(<DevPanel />);
    expect(await screen.findByText(/The dev panel is off/)).toHaveTextContent(
      "BLEEP_DEV_PANEL=true",
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("shows the models, budgets, prompts, cost and guard log when it is on", async () => {
    stubRoutes(json({ enabled: true }));
    render(<DevPanel />);
    for (const title of ["Models", "Budgets", "Prompts", "Cost", "Guard log"]) {
      expect(await screen.findByRole("region", { name: title })).toBeInTheDocument();
    }
    expect(await screen.findByLabelText("Model")).toHaveValue("openai/gpt-6-luna");
    expect(await screen.findByRole("textbox", { name: /Text of/ })).toBeInTheDocument();
  });

  it("saves the budgets from the view the models' save returned, one save at a time", async () => {
    const fetchMock = stubRoutes(json({ enabled: true }));
    const afterModels = settingsView({
      version: "v2",
      overrides: { models: { provider: "local" } },
    });
    render(<DevPanel />);
    const provider = await screen.findByLabelText("Provider");

    fetchMock.mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/settings" && init?.method === "PUT") return json(afterModels);
      if (path === "/api/settings") return json(settingsView());
      if (path === "/api/dev/prompts") return json([prompt()]);
      if (path.startsWith("/api/dev/costs"))
        return json({
          incidents: [],
          days: [],
          daily_cap_usd: 1,
          spent_today_usd: 0,
          unpriced_today: 0,
        });
      return json([]);
    });
    await userEvent.selectOptions(provider, "local");
    await userEvent.click(screen.getByRole("button", { name: "Save the models" }));
    const replans = screen.getByLabelText("Trips back to Propose per run");
    await userEvent.clear(replans);
    await userEvent.type(replans, "1");
    await userEvent.click(screen.getByRole("button", { name: "Save the budgets" }));

    const puts = fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT");
    expect(puts.map(([, init]) => init?.headers)).toMatchObject([
      { "if-match": '"v1"' },
      { "if-match": '"v2"' },
    ]);
  });

  it("after a 409 keeps only the person's edits, so a re-save never puts back an old value", async () => {
    // Here the cap is 1.0; another tab lowers it to 0.5 while the person edits the replans.
    const lowered = settingsView({
      version: "v2",
      budgets: { ...BUDGETS, daily_cost_usd: 0.5 },
      overrides: { budgets: { daily_cost_usd: 0.5 } },
    });
    const reads = [json(settingsView()), json(lowered)];
    const puts = [json({ detail: "the settings changed", version: "v2" }, 409), json(lowered)];
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const path = String(input);
      if (path === "/api/dev") return json({ enabled: true });
      if (path === "/api/settings") {
        return (init?.method === "PUT" ? puts : reads).shift() ?? json({}, 500);
      }
      if (path === "/api/dev/prompts") return json([prompt()]);
      if (path.startsWith("/api/dev/costs"))
        return json({
          incidents: [],
          days: [],
          daily_cap_usd: 1,
          spent_today_usd: 0,
          unpriced_today: 0,
        });
      return json([]);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<DevPanel />);

    const replans = await screen.findByLabelText("Trips back to Propose per run");
    await userEvent.clear(replans);
    await userEvent.type(replans, "5");
    await userEvent.click(screen.getByRole("button", { name: "Save the budgets" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Not saved");
    expect(screen.getByLabelText("Daily cost cap (USD)")).toHaveValue(0.5);
    expect(replans).toHaveValue(5);
    await userEvent.click(screen.getByRole("button", { name: "Save the budgets" }));

    const sent = fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT");
    expect(sent).toHaveLength(2);
    expect(JSON.parse(String(sent[1][1]?.body))).toEqual({
      budgets: { daily_cost_usd: 0.5, replans: 5 },
    });
    expect(sent[1][1]?.headers).toMatchObject({ "if-match": '"v2"' });
  });
});
