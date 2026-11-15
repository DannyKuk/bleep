// One pair of screenshots of a page: node look.mjs <path> <name> [full]
import { goto, openPair, shot } from "./lib.mjs";

const [path = "/", name = "look", full] = process.argv.slice(2);
const { browser, pages } = await openPair();
await goto(pages, path, 2500);
await shot(pages, name, { fullPage: full === "full" });
await browser.close();
