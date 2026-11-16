import type { SuiteKind } from "./extract";

/** Where each suite's numbers come from: the raw batches in docs/evals/data/ (without their
 * extension) and the dated report that reads them. A new round reaches the page by changing these
 * and running `npm test -- -u lib/benchmarks`, which rewrites `data/` from the raw files. */
export type Source = { kind: SuiteKind; batches: string[]; report: string };

export const SOURCES: Record<SuiteKind, Source> = {
  faults: {
    kind: "faults",
    batches: ["scenarios-20261109-075810"],
    report: "docs/evals/scenarios-2026-11-09.md",
  },
  attacks: {
    kind: "attacks",
    batches: ["injection-20261106-091504"],
    report: "docs/evals/planted-markers-2026-11-06.md",
  },
  talk: {
    kind: "talk",
    batches: ["talk-20261113-121701"],
    report: "docs/evals/talk-links-2026-11-13.md",
  },
  look: {
    kind: "look",
    batches: ["look-20261116-102350"],
    report: "docs/evals/tool-arg-texts-2026-11-16.md",
  },
  loopback: {
    kind: "loopback",
    batches: ["loopback-20261109-082230"],
    report: "docs/evals/scenarios-2026-11-09.md",
  },
  recovery: {
    kind: "recovery",
    batches: ["recovery-20261104-124904"],
    report: "docs/evals/recovery-line-2026-11-04.md",
  },
  scripted: {
    kind: "scripted",
    batches: ["scripted-propose-20261116-102720"],
    report: "docs/evals/tool-arg-texts-2026-11-16.md",
  },
  docs: {
    kind: "docs",
    batches: ["knowledge-20261103-203626"],
    report: "docs/evals/knowledge-2026-11-03.md",
  },
};
