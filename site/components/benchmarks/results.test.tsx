import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import BenchmarksPage from "~/app/benchmarks/page";
import { type ModelResult, formatCost, longDate, round } from "~/lib/benchmarks/round";

import { Results } from "./results";

const model = (overrides: Partial<ModelResult>): ModelResult => ({
  ...round.models[0],
  ...overrides,
});

describe("the results", () => {
  it("shows every model's numbers from the data file", () => {
    render(<Results models={round.models} />);
    const table = screen.getByRole("table");
    for (const result of round.models) {
      const row = within(table).getByRole("row", { name: new RegExp(result.name) });
      expect(row).toHaveTextContent(`${result.secondsToDiagnosis} s`);
      expect(row).toHaveTextContent(formatCost(result.costPerRun));
      expect(row).toHaveTextContent(`${result.unsure} of ${result.runs}`);
      expect(row).toHaveTextContent(result.route);
    }
  });

  it("names the case a model missed, and an attack that never reached it", () => {
    const faults = Object.fromEntries(
      Object.keys(round.models[0].faults).map((name) => [
        name,
        { rootCause: true, exactPlan: name !== "misroute", unnecessaryAction: name === "misroute" },
      ]),
    );
    const attacks = Object.fromEntries(
      Object.keys(round.models[0].attacks).map((name) => [
        name,
        { resisted: true, reached: name !== "docs-restart-db" },
      ]),
    );
    render(<Results models={[model({ name: "tester", faults, attacks })]} />);
    const row = within(screen.getByRole("table")).getByRole("row", { name: /tester/ });

    const total = Object.keys(faults).length;
    expect(row).toHaveTextContent(`${total - 1} of ${total}`);
    expect(row).toHaveTextContent("missed misroute");
    expect(row).toHaveTextContent("on misroute");
    expect(row).toHaveTextContent("1 never reached the model");
  });

  it("shows a local model's cost as nothing", () => {
    expect(formatCost(0)).toBe("$0");
    expect(formatCost(0.0032)).toBe("$0.0032");
  });
});

describe("the benchmarks page", () => {
  it("states the date, the judge, the commit and its limits", () => {
    render(<BenchmarksPage />);
    const intro = screen.getByText(/models ran the same/);
    expect(intro).toHaveTextContent(longDate(round.date));
    expect(intro).toHaveTextContent(round.judge);
    for (const result of round.models) expect(intro).toHaveTextContent(result.commit);
    expect(screen.getByRole("heading", { name: "Read the round with these limits" })).toBeVisible();
    expect(screen.getAllByText(/One run per case/)[0]).toBeVisible();
    expect(screen.getByText(/A Claude model judged/)).toBeVisible();
  });

  it("marks its own link in the header as the current page", () => {
    render(<BenchmarksPage />);
    const nav = screen.getByRole("navigation", { name: "Page" });
    expect(within(nav).getByRole("link", { name: "Benchmarks" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(nav).getByRole("link", { name: "Flow" })).not.toHaveAttribute("aria-current");
  });
});
