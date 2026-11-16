import { render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SmoothScroll } from "./smooth-scroll";

describe("smooth scrolling", () => {
  afterEach(() => document.documentElement.classList.remove("smooth"));

  it("is off while the page loads and its first jump happens, and on after", () => {
    vi.useFakeTimers({ toFake: ["requestAnimationFrame"] });
    render(<SmoothScroll />);
    expect(document.documentElement.classList.contains("smooth")).toBe(false);
    vi.advanceTimersToNextFrame();
    expect(document.documentElement.classList.contains("smooth")).toBe(true);
    vi.useRealTimers();
  });
});
