import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CostTables, costText } from "@/components/dev/costs";
import type { CostView } from "@/lib/api-types";

const usage = { calls: 2, tokens_in: 1500, tokens_out: 150, usd: 0.375, unpriced: 0 };

const COSTS: CostView = {
  daily_cap_usd: 1,
  spent_today_usd: 0.375,
  days: [{ day: "2026-11-03", ...usage, calls: 3, tokens_in: 1900, unpriced: 1 }],
  incidents: [
    {
      incident_id: "inc_shop",
      title: "container died on a/shop",
      status: "open",
      opened_at: "2026-11-03T10:00:00Z",
      trace_url: "http://localhost:3001/project/bleep/traces/abc",
      ...usage,
    },
    {
      incident_id: "inc_worker",
      title: "container died on a/worker",
      status: "resolved",
      opened_at: "2026-11-03T10:00:00Z",
      trace_url: null,
      ...usage,
      usd: null,
      unpriced: 1,
      calls: 1,
    },
    {
      incident_id: "not-an-incident",
      title: null,
      status: null,
      opened_at: null,
      trace_url: "javascript:alert(1)",
      ...usage,
      usd: 0,
    },
  ],
};

describe("CostTables", () => {
  it("shows today's spend against the cap and the cost per day", () => {
    render(<CostTables costs={COSTS} />);
    expect(screen.getByText("Today: $0.38 of the $1.00 daily cap")).toBeInTheDocument();
    const [, row] = within(screen.getByRole("table", { name: "Cost per day" })).getAllByRole("row");
    expect(row).toHaveTextContent("2026-11-033");
    expect(row).toHaveTextContent("1,900");
    expect(row).toHaveTextContent("$0.38 + 1 unpriced");
  });

  it("links each incident and its trace when there is one", () => {
    render(<CostTables costs={COSTS} />);
    const table = screen.getByRole("table", { name: "Cost per incident" });
    const [, shop, worker, other] = within(table).getAllByRole("row");

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
  });

  it("says so when nothing has called a model", () => {
    render(<CostTables costs={{ ...COSTS, days: [], incidents: [] }} />);
    expect(screen.getByText("No model calls in the last 30 days.")).toBeInTheDocument();
    expect(screen.getByText("No incident has called a model yet.")).toBeInTheDocument();
  });

  it("writes a cost below a cent with four decimals", () => {
    expect(costText({ ...usage, usd: 0.0042 })).toBe("$0.0042");
    expect(costText({ ...usage, usd: 0 })).toBe("$0.00");
  });
});
