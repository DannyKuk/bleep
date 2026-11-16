import { describe, expect, it } from "vitest";

import { followBy } from "./follow";

const screenHeight = 1000;
const onScreen = { top: -2000, bottom: 2000 };
const box = (top: number) => ({ top, bottom: top + 72 });

describe("following the walk", () => {
  it("leaves the page alone while the step is inside the band", () => {
    expect(followBy(box(200), onScreen, screenHeight)).toBe(0);
    expect(followBy(box(800), onScreen, screenHeight)).toBe(0);
  });

  it("puts a step that left the band back at its top, so the next steps fit below", () => {
    expect(followBy(box(880), onScreen, screenHeight)).toBe(880 - 164);
    expect(followBy(box(-300), onScreen, screenHeight)).toBe(-300 - 164);
  });

  it("does not pull back a visitor who scrolled away from the diagram", () => {
    expect(followBy(box(3000), { top: 2500, bottom: 5000 }, screenHeight)).toBe(0);
    expect(followBy(box(-900), { top: -2000, bottom: 40 }, screenHeight)).toBe(0);
  });
});
