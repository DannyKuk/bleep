import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { CHAT_STEPS } from "./chat";
import { STEPS } from "./flow";
import { FlowSwitch } from "./flow-switch";

const tab = (name: string) => screen.getByRole("tab", { name });
const shows = (text: string) => screen.queryAllByText(text).length > 0;

describe("the flow page's switch", () => {
  afterEach(() => window.history.replaceState(null, "", "/flow"));

  it("shows the incident first", () => {
    window.history.replaceState(null, "", "/flow");
    render(<FlowSwitch />);
    expect(tab("An incident").getAttribute("aria-selected")).toBe("true");
    expect(shows(STEPS.detect.text)).toBe(true);
    expect(shows(CHAT_STEPS.write.text)).toBe(false);
  });

  it("opens the chat from #message", () => {
    window.history.replaceState(null, "", "/flow#message");
    render(<FlowSwitch />);
    expect(tab("A message").getAttribute("aria-selected")).toBe("true");
    expect(shows(CHAT_STEPS.write.text)).toBe(true);
    expect(shows(STEPS.detect.text)).toBe(false);
  });

  it("puts the choice in the URL, and takes it out again", () => {
    render(<FlowSwitch />);
    fireEvent.click(tab("A message"));
    expect(window.location.hash).toBe("#message");
    expect(shows(CHAT_STEPS.write.text)).toBe(true);
    fireEvent.click(tab("An incident"));
    expect(window.location.hash).toBe("");
    expect(shows(STEPS.detect.text)).toBe(true);
  });

  it("moves between the tabs with the arrow keys, keeping one in the tab order", () => {
    render(<FlowSwitch />);
    fireEvent.keyDown(tab("An incident"), { key: "ArrowRight" });
    expect(tab("A message").getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(tab("A message"));
    expect(tab("An incident").tabIndex).toBe(-1);
    fireEvent.keyDown(tab("A message"), { key: "ArrowRight" });
    expect(tab("An incident").getAttribute("aria-selected")).toBe("true");
  });

  it("follows a #message link on the same page", () => {
    render(<FlowSwitch />);
    act(() => {
      window.history.replaceState(null, "", "/flow#message");
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    expect(tab("A message").getAttribute("aria-selected")).toBe("true");
  });

  it("switches to the incident from the chat's way into the incident loop", () => {
    window.history.replaceState(null, "", "/flow#message");
    window.scrollBy = () => {};
    render(<FlowSwitch />);
    fireEvent.click(screen.getAllByRole("button", { name: /The incident loop: show/ })[0]!);
    expect(tab("An incident").getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(tab("An incident"));
    expect(shows(STEPS.detect.text)).toBe(true);
  });
});
