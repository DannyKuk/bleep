import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CHAT_DWELL, CHAT_EDGES, CHAT_ORDER, CHAT_STEPS, chatEdgeId } from "./chat";
import { ChatDiagram } from "./chat-diagram";

const panel = () => document.getElementById("chat-step")!;
const walkButton = () =>
  screen.getAllByRole("button", { name: /Walk me through it|Pause the walk/ })[0]!;
const stepButton = (number: number) =>
  screen.getByRole("button", { name: `${number}. ${CHAT_STEPS[CHAT_ORDER[number - 1]!].title}` });

describe("the chat diagram", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("draws every edge of the model", () => {
    const { container } = render(<ChatDiagram onIncident={() => {}} />);
    for (const edge of CHAT_EDGES) {
      const id = chatEdgeId(edge);
      expect(container.querySelector(`[data-edge="${id}"]`), id).not.toBeNull();
    }
  });

  it("gives every step a button and its whole text in the list", () => {
    render(<ChatDiagram onIncident={() => {}} />);
    CHAT_ORDER.forEach((id, index) => {
      expect(
        screen.getByRole("button", { name: `${index + 1}. ${CHAT_STEPS[id].title}` }),
      ).toBeTruthy();
      expect(screen.getAllByText(CHAT_STEPS[id].text).length).toBeGreaterThan(0);
    });
  });

  it("shows a focused step's text beside the diagram", () => {
    render(<ChatDiagram onIncident={() => {}} />);
    act(() => stepButton(4).focus());
    expect(panel().textContent).toContain(CHAT_STEPS.offer.text);
    expect(panel().textContent).toContain("No, or too late: Nothing changes");
  });

  it("stands still until the visitor asks for the walk", () => {
    render(<ChatDiagram onIncident={() => {}} />);
    act(() => vi.advanceTimersByTime(30_000));
    expect(panel().textContent).toContain(CHAT_STEPS.write.text);
  });

  it("walks once to the last step when asked, then stops", () => {
    window.scrollBy = vi.fn();
    render(<ChatDiagram onIncident={() => {}} />);
    act(() => walkButton().click());
    for (const id of CHAT_ORDER.slice(0, -1)) {
      expect(panel().textContent, id).toContain(CHAT_STEPS[id].title);
      act(() => vi.advanceTimersByTime(CHAT_DWELL[id]));
    }
    act(() => vi.advanceTimersByTime(CHAT_DWELL.run + 30_000));
    expect(panel().textContent).toContain(CHAT_STEPS.run.text);
    expect(walkButton().textContent).toBe("Walk me through it");
  });

  it("leads into the incident loop from the diagram and from the list", () => {
    const onIncident = vi.fn();
    render(<ChatDiagram onIncident={onIncident} />);
    const links = screen.getAllByRole("button", {
      name: "The incident loop: show the incident diagram",
    });
    expect(links).toHaveLength(2);
    for (const link of links) fireEvent.click(link);
    expect(onIncident).toHaveBeenCalledTimes(2);
  });
});
