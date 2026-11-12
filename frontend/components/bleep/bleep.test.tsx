import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Bleep } from "@/components/bleep/bleep";
import { MOODS, type Mood } from "@/lib/mood";

function draw(mood: Mood, size: number) {
  const { container, rerender } = render(<Bleep mood={mood} size={size} />);
  const svg = container.querySelector("svg")!;
  const part = (name: string) => svg.querySelector(`[data-part="${name}"]`);
  return { svg, part, rerender };
}

describe("Bleep", () => {
  it.each(MOODS)("draws the full figure in the %s mood from 40 px up", (mood) => {
    const { svg, part } = draw(mood, 40);
    expect(svg).toHaveAttribute("data-mood", mood);
    expect(svg).toHaveAttribute("data-figure", "full");
    expect(svg).toHaveAttribute("width", "40");
    for (const name of ["body", "head", "headset", "eyes", "light", "hand-left", "hand-right"]) {
      expect(part(name), name).not.toBeNull();
    }
  });

  it.each(MOODS)("draws the head only in the %s mood below 40 px", (mood) => {
    const { svg, part } = draw(mood, 39);
    expect(svg).toHaveAttribute("data-mood", mood);
    expect(svg).toHaveAttribute("data-figure", "head");
    expect(part("head")).not.toBeNull();
    expect(part("eyes")).not.toBeNull();
    const figureOnly = ["headset", "hand-left", "hand-right", "body", "light", "question", "ticks"];
    for (const name of figureOnly) {
      expect(part(name), name).toBeNull();
    }
  });

  it("draws the hands after the head, so they are in front of it", () => {
    const { part } = draw("not_sure", 112);
    const head = part("head")!;
    for (const hand of [part("hand-left")!, part("hand-right")!]) {
      expect(head.compareDocumentPosition(hand) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  });

  it.each([
    ["all_good", "ok", "float", "wave"],
    ["working", "warn", "float", "float2"],
    ["watching", "warn", "float", "chin"],
    ["pointing", "warn", "float", "point"],
    ["needs_you", "down", "flailL", "flailR"],
    ["not_sure", "stop", "scratch", "float"],
    ["resting", "ok", "float", "float2"],
  ] as const)("in the %s mood the light is %s and the hands %s and %s", (mood, light, l, r) => {
    const { part } = draw(mood, 132);
    expect(part("light")).toHaveAttribute("data-light", light);
    expect(part("hand-left")).toHaveAttribute("data-motion", l);
    expect(part("hand-right")).toHaveAttribute("data-motion", r);
  });

  it("watches with a hand on its chin and a steady light, unlike working", () => {
    const watching = draw("watching", 132);
    const place = watching.part("hand-right")!.firstElementChild!;
    expect(place.getAttribute("style")).toContain("translate(68px, 76px)");
    expect(watching.part("light")!.getAttribute("class")).not.toMatch(/pulse/);
    expect(draw("working", 132).part("light")!.getAttribute("class")).toMatch(/slowpulse/);
  });

  it("points out to the side with marks flashing beside the hand and a steady light", () => {
    const pointing = draw("pointing", 132);
    const place = pointing.part("hand-right")!.firstElementChild!;
    expect(place.getAttribute("style")).toContain("translate(114px, 58px)");
    expect(pointing.part("light")!.getAttribute("class")).not.toMatch(/pulse/);
    expect(pointing.part("ticks")!.getAttribute("class")).toMatch(/stroke-warn/);
    for (const mood of MOODS) {
      expect(draw(mood, 72).part("ticks") !== null, mood).toBe(mood === "pointing");
    }
  });

  it("floats a question mark only when it is not sure", () => {
    for (const mood of MOODS) {
      expect(draw(mood, 72).part("question") !== null, mood).toBe(mood === "not_sure");
    }
  });

  it("keeps the same figure when the mood changes, so it moves rather than swaps", () => {
    const { svg, part, rerender } = draw("all_good", 132);
    const head = part("head");
    const hand = part("hand-right");
    rerender(<Bleep mood="needs_you" size={132} />);
    expect(svg).toHaveAttribute("data-mood", "needs_you");
    expect(part("head")).toBe(head);
    expect(part("hand-right")).toBe(hand);
    expect(part("hand-right")).toHaveAttribute("data-motion", "flailR");
  });

  it("is decorative without a title and a labelled image with one", () => {
    const { svg } = draw("working", 30);
    expect(svg).toHaveAttribute("aria-hidden", "true");
    render(<Bleep mood="needs_you" size={76} title="Bleep needs you" />);
    expect(screen.getByRole("img", { name: "Bleep needs you" })).toHaveAttribute(
      "data-mood",
      "needs_you",
    );
  });
});
