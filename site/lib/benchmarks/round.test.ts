import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { type ModelResult, round } from "./round";

const DATA = path.join(import.meta.dirname, "../../../docs/evals/data");
const read = (file: string) => readFileSync(path.join(DATA, file), "utf8");
const lines = (file: string) =>
  read(file)
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line));

type Run = {
  id: string;
  diagnosis: { unsure?: boolean } | null;
  root_cause_pass: boolean;
  action_correct: boolean;
  unnecessary_action: unknown;
  seconds_to_incident: number;
  seconds_to_diagnosis: number;
  usd: number | null;
};
type InjectionRun = { case: string; kind: "attack" | "control"; run: Run };

/** The `Passed` and `Payload reached` cells of each case in an injection batch's rendered table:
 * the verdicts as the current scorer gives them (`evals.injection --render` rewrites this file
 * from the stored runs, as #641 did, while the `.jsonl` keeps the verdict of the day). */
function injectionTable(markdown: string) {
  const rows = new Map<string, { passed: boolean; reached: boolean | null }>();
  for (const [, name, passed, reached] of markdown.matchAll(
    /^\| `([^`]+)` \| [^|]+ \| (\d+) of \d+ \| (\d+ of \d+|[\d.]+) \|/gm,
  )) {
    const isCount = /of/.test(reached);
    rows.set(name, {
      passed: Number(passed) > 0,
      reached: isCount ? Number(reached.split(" ")[0]) > 0 : null,
    });
  }
  return rows;
}

/** Everything the page shows for one model, worked out again from its raw batch files. */
function fromRawFiles(model: ModelResult) {
  const info = JSON.parse(read(`${model.scenarios}.info.json`));
  const faults: Run[] = lines(`${model.scenarios}.jsonl`);
  const injection: InjectionRun[] = lines(`${model.injection}.jsonl`);
  const markdown = read(`${model.injection}.md`);
  const [, header] = markdown.match(/^<!-- (.+) -->/) ?? [];
  const table = injectionTable(markdown);
  const runs = [...faults, ...injection.map((i) => i.run)];
  const verdict = (name: string) => {
    const row = table.get(name);
    if (!row) throw new Error(`${model.injection}.md has no row for ${name}`);
    return row;
  };
  const mean = (values: number[]) => values.reduce((a, b) => a + b, 0) / values.length;
  const sorted = <T extends { id?: string; case?: string }>(items: T[]) =>
    [...items].sort((a, b) => (a.id ?? a.case ?? "").localeCompare(b.id ?? b.case ?? ""));
  return {
    model: info.model,
    route: { openrouter: "OpenRouter", anthropic: "Anthropic", local: "local" }[
      info.provider as string
    ],
    commit: info.commit,
    judge: info.judge,
    date: String(info.started_at).slice(0, 10),
    injectionHeader: header,
    faults: Object.fromEntries(
      sorted(faults).map((run) => [
        run.id,
        {
          rootCause: run.root_cause_pass,
          exactPlan: run.action_correct,
          unnecessaryAction: Boolean(run.unnecessary_action),
        },
      ]),
    ),
    attacks: Object.fromEntries(
      sorted(injection.filter((i) => i.kind === "attack")).map((i) => [
        i.case,
        { resisted: verdict(i.case).passed, reached: verdict(i.case).reached },
      ]),
    ),
    controlRight: injection
      .filter((i) => i.kind === "control")
      .every((i) => verdict(i.case).passed),
    unsure: runs.filter((run) => run.diagnosis?.unsure).length,
    runs: runs.length,
    secondsToDiagnosis: Math.round(
      mean(runs.map((run) => run.seconds_to_diagnosis - run.seconds_to_incident)),
    ),
    costPerRun:
      Math.round((runs.reduce((sum, run) => sum + (run.usd ?? 0), 0) / runs.length) * 1e4) / 1e4,
  };
}

describe("the benchmarks data file", () => {
  it.each(round.models.map((model) => [model.name, model] as const))(
    "holds %s's numbers as its raw batch files give them",
    (_name, model) => {
      const { judge, date, injectionHeader, ...computed } = fromRawFiles(model);
      // On a mismatch the diff prints the values from the raw files, ready to paste.
      expect(model).toEqual({
        ...computed,
        name: model.name,
        scenarios: model.scenarios,
        injection: model.injection,
        note: model.note,
      });
      expect(judge).toBe(round.judge);
      expect(date).toBe(round.date);
      expect(injectionHeader).toMatch(
        new RegExp(
          `^${model.model.replace(/[.]/g, "\\.")}, commit ${model.commit}, ${round.date} `,
        ),
      );
    },
  );

  it("has every model run the same cases", () => {
    const [first, ...rest] = round.models;
    for (const model of rest) {
      expect(Object.keys(model.faults)).toEqual(Object.keys(first.faults));
      expect(Object.keys(model.attacks)).toEqual(Object.keys(first.attacks));
      expect(model.runs).toBe(first.runs);
    }
  });

  it("links documents that exist", () => {
    for (const file of [round.report, round.decision]) {
      expect(() => readFileSync(path.join(import.meta.dirname, "../../..", file))).not.toThrow();
    }
  });
});
