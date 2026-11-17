import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { type Read, SUITE_KINDS, extract, roundDetails } from "./extract";
import { round } from "./round";
import { LOOK_STRONGER, SOURCES } from "./sources";

const ROOT = path.join(import.meta.dirname, "../../..");

const read: Read = (file) => {
  try {
    return readFileSync(path.join(ROOT, file), "utf8");
  } catch {
    return null;
  }
};

const json = (value: unknown) => `${JSON.stringify(value, null, 2)}\n`;

// The page imports data/ and never reads the raw files, which the site's image does not hold.
// These tests write data/ with `npm test -- -u lib/benchmarks` and fail when it differs from
// what the raw files say.
describe.each(SUITE_KINDS)("the %s suite", (kind) => {
  const source = SOURCES[kind];

  it("shows what its raw batches say", async () => {
    const { data, details } = extract(kind, read, source.batches);
    await expect(json(data)).toMatchFileSnapshot(`data/${kind}.json`);
    await expect(json(details)).toMatchFileSnapshot(`data/${kind}.runs.json`);
  });

  it("links a report that names each of its batches", () => {
    const report = read(source.report);
    expect(report, source.report).not.toBeNull();
    for (const batch of source.batches) expect(report).toContain(batch);
  });
});

describe("the look cases on a stronger model", () => {
  it("shows what its raw batch says", async () => {
    const { data } = extract("look", read, LOOK_STRONGER.batches);
    await expect(json(data)).toMatchFileSnapshot("data/look-stronger.json");
  });

  it("links a report that names its batch", () => {
    const report = read(LOOK_STRONGER.report);
    expect(report, LOOK_STRONGER.report).not.toBeNull();
    for (const batch of LOOK_STRONGER.batches) expect(report).toContain(batch);
  });
});

describe("the model round", () => {
  it("has each run's detail as its raw batches give it", async () => {
    await expect(json(roundDetails(read, round.models))).toMatchFileSnapshot(
      "data/round.runs.json",
    );
  });
});
