import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const read = (file: string) => readFileSync(path.join(import.meta.dirname, file), "utf8");

/** The `--name: value;` declarations inside the first block that follows `selector`. */
function tokens(css: string, selector: string): Map<string, string> {
  const start = css.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`no ${selector} block`);
  const body = css.slice(css.indexOf("{", start) + 1, css.indexOf("}", start));
  return new Map(
    [...body.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map(([, name, value]) => [name, value.trim()]),
  );
}

const dashboard = read("../../frontend/app/globals.css");
const site = read("../app/globals.css");
const siteDark = site.slice(site.indexOf("@media (prefers-color-scheme: dark)"));

describe("the landing page's colours", () => {
  it.each([
    ["light", tokens(dashboard, ":root"), tokens(site, ":root")],
    ["dark", tokens(dashboard, ".dark"), tokens(siteDark, ":root")],
  ])("match the dashboard's %s tokens", (_theme, theirs, ours) => {
    const shared = [...ours.keys()].filter((name) => theirs.has(name));
    expect(shared.length).toBeGreaterThanOrEqual(20);
    for (const name of shared) expect([name, ours.get(name)]).toEqual([name, theirs.get(name)]);
  });

  it("use the dashboard's icon", () => {
    expect(read("../app/icon.svg")).toBe(read("../../frontend/app/icon.svg"));
  });
});
