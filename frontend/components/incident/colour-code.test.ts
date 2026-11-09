import { describe, expect, it } from "vitest";

import { LOOK, type Mark } from "@/components/incident/colour-code";

const marks = Object.keys(LOOK) as Mark[];

describe("LOOK", () => {
  it("keeps red for a step that failed or where a run stopped, and nothing else", () => {
    const red = marks.filter((mark) => /\bdown\b/.test(LOOK[mark].fill + LOOK[mark].ink));
    expect(red.sort()).toEqual(["failed", "stopped"]);
  });

  it("gives one colour per meaning: green done, accent now, amber a warning", () => {
    expect(LOOK.done.fill).toBe("bg-ok");
    expect(LOOK.current.fill).toBe("bg-accent");
    expect(LOOK.warning.fill).toBe("bg-warn");
    expect(marks.filter((mark) => LOOK[mark].fill === "bg-ok")).toEqual(["done"]);
    expect(marks.filter((mark) => LOOK[mark].fill === "bg-accent")).toEqual(["current"]);
  });

  it("says every state in words, distinct for each", () => {
    const words = marks.map((mark) => LOOK[mark].word);
    expect(words.every((word) => word.length > 0)).toBe(true);
    expect(new Set(words).size).toBe(words.length);
  });

  it("draws a cross for what went wrong and for a no, so colour is not the only cue there", () => {
    expect(marks.filter((mark) => LOOK[mark].cross).sort()).toEqual([
      "declined",
      "failed",
      "stopped",
    ]);
  });
});
