// Two headless browsers on the throwaway Bleep, one light and one dark, kept on the same page so
// every capture is a pair of the same moment (docs/demo.md "The README video").
import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

export const BASE = process.env.BLEEP_VIDEO_URL ?? "http://127.0.0.1:3100";
export const OUT = new URL("../.local/frames/", import.meta.url).pathname;
const STAGE = new URL("../stage.sh", import.meta.url).pathname;

export async function openPair() {
  mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const password = execFileSync(STAGE, ["password"]).toString().trim();
  const pages = {};
  for (const theme of ["light", "dark"]) {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      deviceScaleFactor: 2,
      colorScheme: theme,
    });
    await context.addInitScript((t) => localStorage.setItem("bleep:theme", t), theme);
    const res = await context.request.post(`${BASE}/api/auth/login`, { data: { password } });
    if (!res.ok()) throw new Error(`login ${theme}: ${res.status()}`);
    pages[theme] = await context.newPage();
  }
  return { browser, pages };
}

export async function goto(pages, path, settle = 1500) {
  await Promise.all(Object.values(pages).map((p) => p.goto(BASE + path, { waitUntil: "load" })));
  await pages.light.waitForTimeout(settle);
}

export async function shot(pages, name, opts = {}) {
  const at = new Date().toISOString();
  await Promise.all(
    Object.entries(pages).map(([theme, p]) =>
      p.screenshot({ path: `${OUT}${name}.${theme}.png`, fullPage: opts.fullPage ?? false }),
    ),
  );
  return at;
}
