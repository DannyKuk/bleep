import type { SuiteKind } from "./extract";

/** Where each suite's numbers come from: the raw batches in docs/evals/data/ (without their
 * extension) and the dated report that reads them. A new round reaches the page by changing these
 * and running `npm test -- -u lib/benchmarks`, which rewrites `data/` from the raw files. */
export type Source = { kind: SuiteKind; batches: string[]; report: string };

/** The cases the model under test missed, run again on a stronger model: it shows where the limit
 * is the model's and where it is the case's scoring. Its runs sit beside the missed ones. */
export const STRONGER: Partial<Record<SuiteKind, Source>> = {
  faults: {
    kind: "faults",
    batches: ["scenarios-20261117-093040-e1"],
    report: "docs/evals/scenarios-2026-11-17-3.md",
  },
  look: {
    kind: "look",
    batches: ["look-20261117-083758"],
    report: "docs/evals/showcase-2026-11-17.md",
  },
};

export const SOURCES: Record<SuiteKind, Source> = {
  faults: {
    kind: "faults",
    batches: [
      "scenarios-20261117-082059-e1",
      "scenarios-20261117-082059-e2",
      "scenarios-20261117-082059-e3",
      "scenarios-20261117-082059-e4",
      "scenarios-20261117-082723",
    ],
    report: "docs/evals/showcase-2026-11-17.md",
  },
  attacks: {
    kind: "attacks",
    batches: [
      "injection-20261117-083748-e1",
      "injection-20261117-084044-e2",
      "injection-20261117-083802-e3",
      "injection-20261117-084050-e4",
    ],
    report: "docs/evals/showcase-2026-11-17.md",
  },
  talk: {
    kind: "talk",
    batches: ["talk-20261117-075651"],
    report: "docs/evals/showcase-2026-11-17.md",
  },
  look: {
    kind: "look",
    batches: ["look-20261117-080056"],
    report: "docs/evals/showcase-2026-11-17.md",
  },
  loopback: {
    kind: "loopback",
    batches: ["loopback-20261117-081749"],
    report: "docs/evals/showcase-2026-11-17.md",
  },
  recovery: {
    kind: "recovery",
    batches: ["recovery-20261117-075614"],
    report: "docs/evals/showcase-2026-11-17.md",
  },
  scripted: {
    kind: "scripted",
    batches: ["scripted-propose-20261117-081302"],
    report: "docs/evals/showcase-2026-11-17.md",
  },
  docs: {
    kind: "docs",
    batches: ["knowledge-20261117-075613", "knowledge-20261117-082018-multilingual"],
    report: "docs/evals/showcase-2026-11-17.md",
  },
};
