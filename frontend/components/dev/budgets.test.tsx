import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { BudgetsForm } from "@/components/dev/budgets";
import { BUDGETS, json, sentBody, settingsView, stubBackend } from "@/components/dev/fixtures";

describe("BudgetsForm", () => {
  it("shows the budgets in force and saves the changed ones as numbers", async () => {
    const saved = settingsView({
      budgets: { ...BUDGETS, daily_cost_usd: 2.5, verify_loops: 1 },
      overrides: { budgets: { daily_cost_usd: 2.5, verify_loops: 1 } },
    });
    const fetchMock = stubBackend(json(saved));
    const onSaved = vi.fn();
    render(
      <BudgetsForm
        view={settingsView({
          overrides: { budgets: { verify_loops: 1 } },
          budgets: { ...BUDGETS, verify_loops: 1 },
        })}
        onSaved={onSaved}
        onStale={vi.fn(async () => undefined)}
      />,
    );
    expect(screen.getByLabelText("Tool calls per Investigate round")).toHaveValue(8);

    const cap = screen.getByLabelText("Daily cost cap (USD)");
    await userEvent.clear(cap);
    await userEvent.type(cap, "2.5");
    await userEvent.click(screen.getByRole("button", { name: "Save the budgets" }));

    expect(fetchMock.mock.calls[0][0]).toBe("/api/settings");
    expect(sentBody(fetchMock, 0)).toEqual({ budgets: { verify_loops: 1, daily_cost_usd: 2.5 } });
    expect(onSaved).toHaveBeenCalledWith(saved);
  });

  it("refuses an empty or out-of-bounds value before sending it", async () => {
    const fetchMock = stubBackend();
    render(
      <BudgetsForm
        view={settingsView()}
        onSaved={vi.fn()}
        onStale={vi.fn(async () => undefined)}
      />,
    );

    const calls = screen.getByLabelText("Tool calls per Investigate round");
    await userEvent.clear(calls);
    await userEvent.type(calls, "500");
    await userEvent.click(screen.getByRole("button", { name: "Save the budgets" }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Tool calls per Investigate round must be a number from 1 to 50.",
    );

    await userEvent.clear(calls);
    await userEvent.click(screen.getByRole("button", { name: "Save the budgets" }));
    expect(screen.getByRole("alert")).toHaveTextContent("must be a number from 1 to 50");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("clears the overrides to bring back the defaults", async () => {
    const fetchMock = stubBackend(json(settingsView()));
    render(
      <BudgetsForm
        view={settingsView({ overrides: { budgets: { replans: 1 } } })}
        onSaved={vi.fn()}
        onStale={vi.fn(async () => undefined)}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Use the defaults" }));
    expect(sentBody(fetchMock, 0)).toEqual({ budgets: {} });
  });

  it("treats 5 retyped as 5.0 as no edit, so another writer's change is not put back", async () => {
    const view = settingsView({ budgets: { ...BUDGETS, verify_loops: 5 } });
    const onStale = vi.fn(async () => undefined);
    const { rerender } = render(<BudgetsForm view={view} onSaved={vi.fn()} onStale={onStale} />);
    const loops = screen.getByLabelText("Verify loops back to Investigate");
    await userEvent.clear(loops);
    await userEvent.type(loops, "5.0");
    expect(screen.getByRole("button", { name: "Save the budgets" })).toBeDisabled();

    rerender(
      <BudgetsForm
        view={settingsView({ budgets: { ...BUDGETS, verify_loops: 4 }, version: "v2" })}
        onSaved={vi.fn()}
        onStale={onStale}
      />,
    );

    expect(screen.getByLabelText("Verify loops back to Investigate")).toHaveValue(4);
  });
});
