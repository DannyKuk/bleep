import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { POSES } from "@/components/bleep/poses";

const css = readFileSync(join(__dirname, "bleep.module.css"), "utf8");

const classNames = new Set([...css.matchAll(/^\.(\w+)\s*[{,]/gm)].map((m) => m[1]));
const keyframes = new Set([...css.matchAll(/@keyframes (\w+)/g)].map((m) => m[1]));

describe("Bleep's motion", () => {
  it("turns every animation and transition off for prefers-reduced-motion", () => {
    const reduced = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"));
    expect(reduced).toMatch(/\.bleep \*/);
    expect(reduced).toMatch(/animation: none !important/);
    expect(reduced).toMatch(/transition: none !important/);
  });

  it("has a class for every motion a pose names", () => {
    for (const pose of Object.values(POSES)) {
      const motions = [pose.eyesMotion, pose.leftMotion, pose.rightMotion, pose.lightMotion];
      for (const motion of [...motions, pose.headMotion].filter(Boolean)) {
        expect(classNames, motion).toContain(motion);
      }
    }
  });

  it("names only keyframes it defines", () => {
    const used = [...css.matchAll(/animation: (\w+) /g)]
      .map((m) => m[1])
      .filter((n) => n !== "none");
    expect(used.length).toBeGreaterThan(0);
    for (const name of used) expect(keyframes, name).toContain(name);
  });
});
