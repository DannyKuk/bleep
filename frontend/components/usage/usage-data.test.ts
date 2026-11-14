import { describe, expect, it } from "vitest";

import {
  addDays,
  dayAxis,
  modelSeries,
  niceMax,
  OTHER_COLOUR,
  reportsNoCache,
  shareOf,
} from "@/components/usage/usage-data";
import type { DayUsage } from "@/lib/api-types";

const row = (day: string, model: string, cost_usd: number): DayUsage => ({
  day,
  model,
  cost_usd,
  incident_calls: 1,
  chat_calls: 0,
});

describe("addDays", () => {
  it("crosses a month and a year, and a clock change", () => {
    expect(addDays("2026-11-01", -1)).toBe("2026-10-31");
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
    expect(addDays("2026-03-29", 1)).toBe("2026-03-30");
    expect(addDays("2026-10-25", 1)).toBe("2026-10-26");
  });
});

describe("dayAxis", () => {
  it("is the range's days, oldest first, ending on the server's today", () => {
    expect(dayAxis(7, "2026-11-13")).toEqual([
      "2026-11-07",
      "2026-11-08",
      "2026-11-09",
      "2026-11-10",
      "2026-11-11",
      "2026-11-12",
      "2026-11-13",
    ]);
    expect(dayAxis(1, "2026-11-13")).toEqual(["2026-11-13"]);
    expect(dayAxis(90, "2026-11-13")).toHaveLength(90);
  });

  it("counts from the server's day, never from the browser's", () => {
    expect(dayAxis(1, "2026-11-13")).toEqual(["2026-11-13"]);
    expect(dayAxis(30, "2026-11-13")[0]).toBe("2026-10-15");
  });
});

describe("niceMax", () => {
  it.each([
    [0, 1],
    [0.0042, 0.005],
    [0.8, 1],
    [1.2, 2],
    [3, 5],
    [7, 10],
    [11, 20],
    [100, 100],
  ])("rounds %s up to %s", (value, top) => {
    expect(niceMax(value)).toBeCloseTo(top);
  });
});

describe("modelSeries", () => {
  const rows = [row("2026-11-12", "b", 2), row("2026-11-12", "a", 1), row("2026-11-13", "a", 3)];

  it("keeps each model's colour and orders by colour, not by cost", () => {
    const series = modelSeries(rows, { a: 1, b: 2 }, (r) => r.cost_usd, "Other");
    expect(series.map((s) => [s.key, s.label, s.colour])).toEqual([
      ["model-1", "a", "var(--viz-1)"],
      ["model-2", "b", "var(--viz-2)"],
    ]);
    expect(series[0].values.get("2026-11-13")).toBe(3);
  });

  it("folds the models without a colour into one other series", () => {
    const series = modelSeries(rows, { a: 1, b: null }, (r) => r.cost_usd, "Other");
    expect(series.map((s) => s.label)).toEqual(["a", "Other"]);
    expect(series[1].colour).toBe(OTHER_COLOUR);
  });
});

describe("numbers", () => {
  it("never divides by nothing", () => {
    expect(shareOf(1, 0)).toBe(0);
    expect(shareOf(1, 4)).toBe(0.25);
  });

  it("reads no cache tokens at all as a provider that reports none", () => {
    const base = { cache_read_tokens: 0, cache_write_tokens: 0 };
    expect(reportsNoCache(base as never)).toBe(true);
    expect(reportsNoCache({ ...base, cache_write_tokens: 5 } as never)).toBe(false);
  });
});
