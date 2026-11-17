import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { type Read, SUITE_KINDS, extract, roundDetails } from "./extract";
import { round } from "./round";
import { SOURCES, STRONGER } from "./sources";

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

describe.each(Object.values(STRONGER))("the $kind cases on a stronger model", (source) => {
  it("shows what its raw batch says", async () => {
    const { data, details } = extract(source.kind, read, source.batches);
    await expect(json(data)).toMatchFileSnapshot(`data/${source.kind}-stronger.json`);
    await expect(json(details)).toMatchFileSnapshot(`data/${source.kind}-stronger.runs.json`);
  });

  it("links a report that names its batch", () => {
    const report = read(source.report);
    expect(report, source.report).not.toBeNull();
    for (const batch of source.batches) expect(report).toContain(batch);
  });

  it("holds only cases the model under test missed", () => {
    const { data } = extract(source.kind, read, source.batches);
    const missed = extract(source.kind, read, SOURCES[source.kind].batches).data.cases.filter((c) =>
      c.runs.includes("fail"),
    );
    for (const c of data.cases) expect(missed.map((m) => m.name)).toContain(c.name);
  });
});

describe("the model round", () => {
  it("has each run's detail as its raw batches give it", async () => {
    await expect(json(roundDetails(read, round.models))).toMatchFileSnapshot(
      "data/round.runs.json",
    );
  });
});
