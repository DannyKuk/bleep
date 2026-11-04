import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PROGRESS_STEPS, type StepState } from "@/components/incident/progress";
import { ProgressBar } from "@/components/incident/progress-bar";

const STATES: StepState[] = [
  "done",
  "done",
  "done",
  "done",
  "declined",
  "failed",
  "stopped",
  "current",
];

describe("ProgressBar", () => {
  it("says each step's state in words, never by colour alone", () => {
    render(
      <ProgressBar
        steps={PROGRESS_STEPS.map(({ label }, index) => ({ label, state: STATES[index] }))}
      />,
    );
    const items = within(screen.getByRole("list", { name: "Progress" })).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("Gather, done");
    expect(items[4]).toHaveTextContent("Your yes, you said no");
    expect(items[5]).toHaveTextContent("Fix, failed");
    expect(items[6]).toHaveTextContent("Check, stopped here");
    expect(items[7]).toHaveTextContent("Report, now");
    expect(items[7]).toHaveAttribute("aria-current", "step");
    for (const marked of [items[4], items[5], items[6]]) {
      expect(marked.querySelector("svg")).not.toBeNull();
    }
    expect(items[0].querySelector("svg")).toBeNull();
  });
});
