import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LOOK } from "@/components/incident/colour-code";
import { event } from "@/components/incident/fixtures";
import { PROGRESS_STEPS, progressSteps, type StepState } from "@/components/incident/progress";
import { ProgressBar } from "@/components/incident/progress-bar";
import type { IncidentEvent, IncidentStatus } from "@/lib/api-types";

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

const ran = (...nodes: string[]) =>
  nodes.flatMap((node) => [event("node_started", { node }), event("node_finished", { node })]);

/** Each segment as a screen reader hears it, with the state it is drawn in. */
function segments(events: IncidentEvent[], status: IncidentStatus) {
  render(<ProgressBar steps={progressSteps(events, status)} />);
  return within(screen.getByRole("list", { name: "Progress" }))
    .getAllByRole("listitem")
    .map((item) => [item.textContent, item.getAttribute("data-state")]);
}

describe("ProgressBar", () => {
  it("says each step's state in words, never by colour alone", () => {
    render(
      <ProgressBar steps={PROGRESS_STEPS.map((node, index) => ({ node, state: STATES[index] }))} />,
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

  it("draws each segment from the shared colour code", () => {
    render(
      <ProgressBar steps={PROGRESS_STEPS.map((node, index) => ({ node, state: STATES[index] }))} />,
    );
    const items = within(screen.getByRole("list", { name: "Progress" })).getAllByRole("listitem");
    items.forEach((item, index) => {
      expect(item.querySelector("span[aria-hidden]")).toHaveClass(LOOK[STATES[index]].fill);
    });
  });

  it("shows a failed run's failed fix and the step it stopped at", () => {
    const events = [
      ...ran("gather", "investigate", "diagnose", "propose", "approval"),
      event("node_started", { node: "execute" }),
      event("action", { action: "docker.start", target: "shop-web-1", status: "failed" }),
      event("node_finished", { node: "execute" }),
      event("node_started", { node: "verify" }),
    ];
    expect(segments(events, "failed").slice(4)).toEqual([
      ["Your yes, done", "done"],
      ["Fix, failed", "failed"],
      ["Check, stopped here", "stopped"],
      ["Report, not done", "todo"],
    ]);
  });

  it("shows a declined plan as a no on Your yes, not as a failure", () => {
    const events = [
      ...ran("gather", "investigate", "diagnose", "propose"),
      event("node_started", { node: "approval" }),
      event("decision", { kind: "reject", text: null, via: "telegram" }),
      event("node_finished", { node: "approval" }),
    ];
    expect(segments(events, "closed").slice(4)).toEqual([
      ["Your yes, you said no", "declined"],
      ["Fix, not done", "todo"],
      ["Check, not done", "todo"],
      ["Report, done", "done"],
    ]);
  });

  it("shows a resolved incident done throughout", () => {
    const events = [
      ...ran("gather", "investigate", "diagnose", "propose", "approval", "execute"),
      event("node_started", { node: "verify" }),
      event("verification", { ok: true, attempt: 1, checks: [] }),
      event("node_finished", { node: "verify" }),
      ...ran("report"),
    ];
    expect(segments(events, "resolved").map(([, state]) => state)).toEqual(Array(8).fill("done"));
  });

  it("shows a closed incident's unfinished step as not done, never red", () => {
    const events = [...ran("gather"), event("node_started", { node: "investigate" })];
    expect(segments(events, "closed").slice(0, 3)).toEqual([
      ["Gather, done", "done"],
      ["Investigate, not done", "todo"],
      ["Diagnose, not done", "todo"],
    ]);
  });
});
