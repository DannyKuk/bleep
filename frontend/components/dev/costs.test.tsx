import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CostTables, costText } from "@/components/dev/costs";
import type { CostView, IncidentCost } from "@/lib/api-types";
import { MESSAGES } from "@/lib/i18n";

const UNPRICED_NOTE = MESSAGES.en.dev.costs.note;
const usage = { calls: 2, tokens_in: 1500, tokens_out: 150, usd: 0.375, unpriced: 0 };

const COSTS: CostView = {
  daily_cap_usd: 1,
  spent_today_usd: 0.375,
  unpriced_today: 1,
  days: [{ day: "2026-11-03", ...usage, calls: 3, tokens_in: 1900, unpriced: 1 }],
  models: [
    { model: "openai/gpt-6-luna", ...usage, calls: 5, tokens_in: 12000 },
    { model: "claude-sonnet-5-5", ...usage, usd: null, unpriced: 2 },
  ],
  models_today: [{ model: "claude-sonnet-5-5", ...usage, usd: null, unpriced: 2 }],
  incidents: [
    {
      incident_id: "inc_shop",
      title: "container died on a/shop",
      status: "open",
      opened_at: "2026-11-03T10:00:00Z",
      trace_url: "http://localhost:3001/project/bleep/traces/abc",
      prompt_overrides: { "graph/propose": "0123456789abcdef" },
      models: ["openai/gpt-6-luna", "claude-sonnet-5-5"],
      ...usage,
    },
    {
      incident_id: "inc_worker",
      title: "container died on a/worker",
      status: "resolved",
      opened_at: "2026-11-03T10:00:00Z",
      trace_url: null,
      prompt_overrides: {},
      models: ["mystery/m"],
      ...usage,
      usd: null,
      unpriced: 1,
      calls: 1,
    },
    {
      incident_id: "dev-panel",
      title: null,
      status: null,
      opened_at: null,
      trace_url: null,
      prompt_overrides: null,
      models: [],
      ...usage,
    },
    {
      incident_id: "not-an-incident",
      title: null,
      status: null,
      opened_at: null,
      trace_url: "javascript:alert(1)",
      prompt_overrides: null,
      models: [],
      ...usage,
      usd: 0,
    },
  ],
};

describe("CostTables", () => {
  it("shows today's spend against the cap and the cost per day", () => {
    render(<CostTables costs={COSTS} />);
    expect(screen.getByText(/^Today: \$0\.38 of the \$1\.00 daily cap\./)).toHaveTextContent(
      "Today: $0.38 of the $1.00 daily cap. 1 call today has no known price and is not in that sum.",
    );
    const [, row] = within(screen.getByRole("table", { name: "Cost per day" })).getAllByRole("row");
    expect(row).toHaveTextContent("2026-11-033");
    expect(row).toHaveTextContent("1,900");
    expect(row).toHaveTextContent("$0.38 + 1 unpriced");
  });

  it("links each incident and its trace when there is one", () => {
    render(<CostTables costs={COSTS} />);
    const table = screen.getByRole("table", { name: "Cost per incident" });
    const [, shop, worker, devPanel, other] = within(table).getAllByRole("row");

    expect(devPanel).toHaveTextContent("dev-panel");
    expect(within(devPanel).queryByRole("link")).not.toBeInTheDocument();
    expect(within(shop).getByRole("link", { name: "container died on a/shop" })).toHaveAttribute(
      "href",
      "/incidents/inc_shop",
    );
    const trace = within(shop).getByRole("link", { name: "Trace of container died on a/shop" });
    expect(trace).toHaveAttribute("href", "http://localhost:3001/project/bleep/traces/abc");
    expect(trace).toHaveAttribute("target", "_blank");

    expect(worker).toHaveTextContent("unknown + 1 unpriced");
    expect(within(worker).getAllByRole("link")).toHaveLength(1);

    expect(other).toHaveTextContent("not-an-incident");
    expect(within(other).queryByRole("link")).not.toBeInTheDocument();
    expect(within(other).getByText("javascript:alert(1)")).toBeInTheDocument();
  });

  it("says plainly that dollars leave out the unpriced calls", () => {
    render(<CostTables costs={{ ...COSTS, unpriced_today: 0 }} />);
    expect(screen.getByText("Today: $0.38 of the $1.00 daily cap.")).toBeInTheDocument();
    expect(screen.getByText(UNPRICED_NOTE)).toBeInTheDocument();
  });

  it("leaves the note out when every call has a price", () => {
    const priced = (row: IncidentCost) => ({ ...row, usd: 0, unpriced: 0 });
    render(
      <CostTables
        costs={{
          ...COSTS,
          unpriced_today: 0,
          days: [{ day: "2026-11-03", ...usage }],
          incidents: COSTS.incidents.map(priced),
        }}
      />,
    );
    expect(screen.queryByText(UNPRICED_NOTE)).not.toBeInTheDocument();
  });

  it("shows the prompt overrides each incident's run started with", () => {
    render(<CostTables costs={COSTS} />);
    const table = screen.getByRole("table", { name: "Cost per incident" });
    const [, shop, worker, , other] = within(table).getAllByRole("row");
    expect(within(shop).getByText("graph/propose")).toHaveAttribute(
      "title",
      "override version 0123456789abcdef",
    );
    expect(shop).toHaveTextContent("graph/propose 01234567");
    expect(worker).toHaveTextContent("none");
    expect(other).toHaveTextContent("not recorded");
  });

  it("shows each model's calls, tokens and cost, and what it did today", () => {
    render(<CostTables costs={COSTS} />);
    const [, luna, sonnet] = within(
      screen.getByRole("table", { name: "Cost per model" }),
    ).getAllByRole("row");
    expect(luna).toHaveTextContent("openai/gpt-6-luna");
    expect(luna).toHaveTextContent("12,000");
    expect(luna).toHaveTextContent("$0.38");
    expect(luna).toHaveTextContent("none");
    expect(sonnet).toHaveTextContent("claude-sonnet-5-5");
    expect(sonnet).toHaveTextContent("unknown + 2 unpriced");
    expect(sonnet).toHaveTextContent("2 / unknown + 2 unpriced");
  });

  it("names the models each incident's calls used", () => {
    render(<CostTables costs={COSTS} />);
    const table = screen.getByRole("table", { name: "Cost per incident" });
    const [, shop, worker, devPanel] = within(table).getAllByRole("row");
    expect(shop).toHaveTextContent("openai/gpt-6-luna");
    expect(shop).toHaveTextContent("claude-sonnet-5-5");
    expect(worker).toHaveTextContent("mystery/m");
    expect(within(devPanel).queryByText("mystery/m")).not.toBeInTheDocument();
  });

  it("says so when nothing has called a model", () => {
    render(
      <CostTables costs={{ ...COSTS, days: [], models: [], models_today: [], incidents: [] }} />,
    );
    expect(screen.getAllByText("No model calls in the last 30 days.")).toHaveLength(2);
    expect(screen.getByText("No incident has called a model yet.")).toBeInTheDocument();
  });

  it("writes a cost below a cent with four decimals", () => {
    expect(costText({ ...usage, usd: 0.0042 }, "en")).toBe("$0.0042");
    expect(costText({ ...usage, usd: 0 }, "en")).toBe("$0.00");
  });
});
