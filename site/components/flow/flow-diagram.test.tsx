import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DWELL, EDGES, STEP_ORDER, STEPS, edgeId } from "./flow";
import { FlowDiagram } from "./flow-diagram";

const panel = () => document.getElementById("flow-step")!;
const walkButton = () =>
  screen.getAllByRole("button", { name: /Walk me through it|Pause the walk/ })[0]!;
const stepButton = (number: number) =>
  screen.getByRole("button", { name: `${number}. ${STEPS[STEP_ORDER[number - 1]!].title}` });

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

  it("stands still until the visitor asks for the walk", () => {
    render(<FlowDiagram />);
    act(() => vi.advanceTimersByTime(30_000));
    expect(panel().textContent).toContain(STEPS.detect.text);
  });

  it("walks once to the report when asked, then stops", () => {
    window.scrollBy = vi.fn();
    render(<FlowDiagram />);
    act(() => walkButton().click());
    for (const id of STEP_ORDER.slice(0, -1)) {
      expect(panel().textContent, id).toContain(STEPS[id].title);
      act(() => vi.advanceTimersByTime(DWELL[id]));
    }
    expect(panel().textContent).toContain(STEPS.report.text);
    act(() => vi.advanceTimersByTime(DWELL.report + 30_000));
    expect(panel().textContent).toContain(STEPS.report.text);
    expect(walkButton().textContent).toBe("Walk me through it");
  });

  it("starts again from the first step after the report", () => {
    render(<FlowDiagram />);
    fireEvent.click(stepButton(10));
    act(() => walkButton().click());
    expect(panel().textContent).toContain(STEPS.detect.text);
  });

  it("stops the walk on a picked step, but not on a step under the pointer", () => {
    render(<FlowDiagram />);
    act(() => walkButton().click());
    fireEvent.mouseEnter(stepButton(7));
    act(() => vi.advanceTimersByTime(DWELL.detect));
    expect(panel().textContent).toContain(STEPS.open.text);

    fireEvent.click(stepButton(9));
    act(() => vi.advanceTimersByTime(30_000));
    expect(panel().textContent).toContain(STEPS.verify.text);
    expect(walkButton().textContent).toBe("Walk me through it");
  });

  it("says where the human step leads", () => {
    render(<FlowDiagram />);
    fireEvent.click(screen.getByRole("button", { name: `7. ${STEPS.approval.title}` }));
    for (const way of ["Yes: Runs the fix", "No: Reports", "You tell it something: Proposes"]) {
      expect(panel().textContent).toContain(way);
    }
  });
});
