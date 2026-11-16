import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DWELL, EDGES, STEP_ORDER, STEPS, edgeId } from "./flow";
import { FlowDiagram } from "./flow-diagram";

const panel = () => document.getElementById("flow-step")!;

describe("the flow diagram", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("draws every edge of the model", () => {
    const { container } = render(<FlowDiagram />);
    for (const edge of EDGES) {
      expect(container.querySelector(`[data-edge="${edgeId(edge)}"]`), edgeId(edge)).not.toBeNull();
    }
  });

  it("gives every step a button and its whole text in the list", () => {
    render(<FlowDiagram />);
    STEP_ORDER.forEach((id, index) => {
      expect(screen.getByRole("button", { name: `${index + 1}. ${STEPS[id].title}` })).toBeTruthy();
      expect(screen.getAllByText(STEPS[id].text).length).toBeGreaterThan(0);
    });
  });

  it("walks from step to step until a step is picked, then stays", () => {
    render(<FlowDiagram />);
    expect(panel().textContent).toContain(STEPS.detect.title);
    act(() => vi.advanceTimersByTime(DWELL.detect));
    expect(panel().textContent).toContain(STEPS.open.title);

    fireEvent.focus(screen.getByRole("button", { name: `9. ${STEPS.verify.title}` }));
    expect(panel().textContent).toContain(STEPS.verify.text);
    act(() => vi.advanceTimersByTime(20_000));
    expect(panel().textContent).toContain(STEPS.verify.text);
    expect(screen.getAllByRole("button", { name: "Walk me through it" }).length).toBeGreaterThan(0);
  });

  it("says where the human step leads", () => {
    render(<FlowDiagram />);
    fireEvent.click(screen.getByRole("button", { name: `7. ${STEPS.approval.title}` }));
    for (const way of ["Yes: Runs the fix", "No: Reports", "You tell it something: Proposes"]) {
      expect(panel().textContent).toContain(way);
    }
  });
});
