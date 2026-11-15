// Films one real db-down run on the throwaway Bleep in light and dark (docs/demo.md "The README
// video"): the overview as the fault hits, the incident page while Bleep investigates, the yes
// (sent from the light browser only, so it is sent once), the fix and its checks, and the overview
// once all is quiet again. Frames and a manifest go to demo/video/.local/frames/run/, the
// incident's API data to demo/video/.local/run/.
//   node run.mjs            start the fault itself and film
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { BASE, OUT, goto, openPair } from "./lib.mjs";

const ROOT = new URL("../../../", import.meta.url).pathname;
const FRAMES = `${OUT}run/`;
const DATA = new URL("../.local/run/", import.meta.url).pathname;
mkdirSync(FRAMES, { recursive: true });
mkdirSync(DATA, { recursive: true });

const { browser, pages } = await openPair();
const api = (path) => pages.light.context().request.get(`${BASE}/api${path}`).then((r) => r.json());
const manifest = [];
const t0 = Date.now();
let n = 0;

async function tick(scene, fullPage) {
  const started = Date.now();
  const name = `${String(n++).padStart(4, "0")}-${scene}`;
  await Promise.all(
    Object.entries(pages).map(async ([theme, p]) => {
      await p.evaluate(() => document.querySelectorAll("main details").forEach((d) => (d.open = true)));
      await p.screenshot({ path: `${FRAMES}${name}.${theme}.png`, fullPage });
    }),
  );
  manifest.push({ name, scene, t: (started - t0) / 1000 });
  const wait = 1000 - (Date.now() - started);
  if (wait > 0) await pages.light.waitForTimeout(wait);
}

async function openIncident() {
  const open = await api("/incidents?limit=5");
  return open.find((i) => !["resolved", "closed", "failed"].includes(i.status));
}

async function until(scene, fullPage, done, { after = 0, limit = 600 } = {}) {
  const end = Date.now() + limit * 1000;
  while (Date.now() < end) {
    await tick(scene, fullPage);
    if (await done()) break;
  }
  const tail = Date.now() + after * 1000;
  while (Date.now() < tail) await tick(scene, fullPage);
}

await goto(pages, "/", 2500);
await tick("quiet", false);
await tick("quiet", false);
console.log("fault on");
execFileSync(`${ROOT}demo/fault.sh`, ["db-down", "on"], { stdio: "inherit" });
manifest.push({ name: null, scene: "fault", t: (Date.now() - t0) / 1000 });

let incident;
await until("overview", false, async () => (incident = await openIncident()) !== undefined, {
  limit: 180,
});
if (!incident) throw new Error("no incident opened");
console.log("incident", incident.id);
// The overview polls every 10 s: keep filming until both themes show it.
await until(
  "overview",
  false,
  async () => {
    const quiet = await Promise.all(
      Object.values(pages).map((p) => p.getByText("All quiet").count()),
    );
    return quiet.every((c) => c === 0);
  },
  { after: 2, limit: 25 },
);

await goto(pages, `/incidents/${incident.id}`, 1500);
await until(
  "investigate",
  true,
  async () => (await api(`/incidents/${incident.id}`)).approval?.status === "pending",
  { after: 5 },
);

const yes = pages.light.getByRole("button", { name: "Yes, go ahead" });
await yes.scrollIntoViewIfNeeded();
await tick("approve", true);
manifest.push({ name: null, scene: "yes", t: (Date.now() - t0) / 1000 });
await yes.click();
console.log("approved");
await until(
  "fix",
  true,
  async () => (await api(`/incidents/${incident.id}`)).incident.status === "resolved",
  { after: 8, limit: 300 },
);

await goto(pages, "/", 2000);
await until(
  "resolved",
  false,
  async () => {
    const quiet = await Promise.all(
      Object.values(pages).map((p) => p.getByText("All quiet").count()),
    );
    return quiet.every((c) => c > 0);
  },
  { after: 3, limit: 40 },
);

writeFileSync(`${FRAMES}manifest.json`, JSON.stringify(manifest, null, 1));
for (const [file, path] of [
  ["incident.json", `/incidents/${incident.id}`],
  ["events.json", `/incidents/${incident.id}/events`],
  ["list.json", "/incidents?limit=20"],
]) {
  writeFileSync(`${DATA}${file}`, JSON.stringify(await api(path), null, 1));
}
await browser.close();
console.log("done", incident.id, manifest.length, "ticks");
