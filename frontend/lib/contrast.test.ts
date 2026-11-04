import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

type Rgb = [number, number, number];

const css = readFileSync(join(__dirname, "../app/globals.css"), "utf8");

function block(selector: string): Record<string, string> {
  const start = css.indexOf(`${selector} {`);
  const body = css.slice(start, css.indexOf("}", start));
  return Object.fromEntries(
    [...body.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]),
  );
}

function parseHex(value: string): Rgb {
  const n = parseInt(value.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function resolve(name: string, tokens: Record<string, string>): Rgb {
  const value = tokens[name];
  if (value === undefined) throw new Error(`token ${name} is not defined`);
  const mix = value.match(/^color-mix\(in srgb, var\((--[\w-]+)\) (\d+)%, var\((--[\w-]+)\)\)$/);
  if (mix) {
    const a = resolve(mix[1], tokens);
    const b = resolve(mix[3], tokens);
    const share = Number(mix[2]) / 100;
    return a.map((c, i) => c * share + b[i] * (1 - share)) as Rgb;
  }
  const ref = value.match(/^var\((--[\w-]+)\)$/);
  if (ref) return resolve(ref[1], tokens);
  if (!value.startsWith("#")) throw new Error(`${name}: ${value} is not a hex colour or a mix`);
  return parseHex(value);
}

function luminance(rgb: Rgb): number {
  const [r, g, b] = rgb.map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(fg: Rgb, bg: Rgb): number {
  const [hi, lo] = [luminance(fg), luminance(bg)].sort((a, b) => b - a);
  return (hi + 0.05) / (lo + 0.05);
}

const light = block(":root");
const themes: [string, Record<string, string>][] = [
  ["light", light],
  ["dark", { ...light, ...block(".dark") }],
];

const TEXT = ["ink", "ink-2", "accent", "ok", "warn", "down", "stop"];
const GROUNDS = ["surface", "bg", "soft"];
const HEALTH = ["ok", "warn", "down", "stop"];

describe.each(themes)("WCAG contrast, %s theme", (_theme, tokens) => {
  const ratio = (fg: string, bg: string) =>
    contrast(resolve(`--${fg}`, tokens), resolve(`--${bg}`, tokens));

  const pairs: [string, string][] = [
    ...TEXT.flatMap((fg) => GROUNDS.map((bg): [string, string] => [fg, bg])),
    ...HEALTH.map((h): [string, string] => [h, `${h}-bg`]),
    ["accent-ink", "accent"],
  ];

  it.each(pairs)("%s on %s is at least 4.5:1", (fg, bg) => {
    expect(ratio(fg, bg)).toBeGreaterThanOrEqual(4.5);
  });

  it("maps every health token onto a Bleep token", () => {
    expect(tokens["--health-unknown"]).toBe("var(--stop)");
    expect(tokens["--health-degraded"]).toBe("var(--warn)");
  });
});
