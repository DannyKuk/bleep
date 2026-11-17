import type { Outcome as RunOutcome } from "~/lib/benchmarks/extract";
import {
  type Attack,
  type Fault,
  type ModelResult,
  count,
  formatCost,
} from "~/lib/benchmarks/round";

import { DotLegend } from "./dot";
import { DotButton, RovingGroup } from "./dot-grid";

type Outcome = "right" | "wrong" | "unmet";

/** Where a column's dots find their runs: the model's batch for that suite. */
type Source = { model: ModelResult; batch: string; column: string };

const RUN: Record<Outcome, RunOutcome> = { right: "pass", wrong: "fail", unmet: "unmet" };
const VERDICT: Record<Outcome, string> = {
  right: "right",
  wrong: "missed",
  unmet: "never reached the model",
};

/** One dot per case run, so a reader sees which case a model missed, not only how many; each
 * opens that run. */
function Dots({ cases, source }: { cases: [string, Outcome][]; source: Source }) {
  return (
    <RovingGroup className="flex gap-2">
      {cases.map(([name, outcome]) => (
        <DotButton
          key={name}
          label={`${source.model.name}, ${name}: ${VERDICT[outcome]}`}
          run={{
            set: "round",
            key: `${source.batch}/${name}/1`,
            title: name,
            subtitle: `${source.model.name}, ${source.column.toLowerCase()}`,
            outcome: RUN[outcome],
            verdict: `${source.column}: ${VERDICT[outcome]}`,
          }}
        />
      ))}
    </RovingGroup>
  );
}

function Score({
  cases,
  source,
  note,
}: {
  cases: [string, Outcome][];
  source: Source;
  note?: string;
}) {
  const right = cases.filter(([, outcome]) => outcome !== "wrong").length;
  const missed = cases.filter(([, outcome]) => outcome === "wrong").map(([name]) => name);
  return (
    <div className="flex flex-col gap-1.5">
      <span className="font-extrabold whitespace-nowrap">
        {right} of {cases.length}
      </span>
      <Dots cases={cases} source={source} />
      {missed.length > 0 && (
        <span className="text-sm [overflow-wrap:anywhere] text-down">
          missed {missed.join(", ")}
        </span>
      )}
      {note && <span className="text-sm text-ink-2">{note}</span>}
    </div>
  );
}

const faultCases = (model: ModelResult, right: (fault: Fault) => boolean) =>
  Object.entries(model.faults).map(
    ([name, fault]) => [name, right(fault) ? "right" : "wrong"] as [string, Outcome],
  );

const attackCases = (model: ModelResult) =>
  Object.entries(model.attacks).map(([name, attack]: [string, Attack]) => [
    name,
    !attack.resisted ? "wrong" : attack.reached ? "right" : "unmet",
  ]) as [string, Outcome][];

function unmetNote(model: ModelResult) {
  const unmet = count(model.attacks, (attack) => attack.resisted && !attack.reached);
  if (unmet === 0) return undefined;
  return `${unmet} never reached the model`;
}

function unnecessary(model: ModelResult) {
  const cases = Object.entries(model.faults)
    .filter(([, fault]) => fault.unnecessaryAction)
    .map(([name]) => name);
  return (
    <div className="flex flex-col gap-1.5">
      <span className="font-extrabold">{cases.length}</span>
      {cases.length > 0 && <span className="text-sm text-down">on {cases.join(", ")}</span>}
    </div>
  );
}

const COLUMNS: { title: string; cell: (model: ModelResult) => React.ReactNode }[] = [
  {
    title: "Cause found",
    cell: (m) => (
      <Score
        cases={faultCases(m, (f) => f.rootCause)}
        source={{ model: m, batch: m.scenarios, column: "Cause found" }}
      />
    ),
  },
  {
    title: "Exact plan",
    cell: (m) => (
      <Score
        cases={faultCases(m, (f) => f.exactPlan)}
        source={{ model: m, batch: m.scenarios, column: "Exact plan" }}
      />
    ),
  },
  { title: "Unneeded actions", cell: unnecessary },
  {
    title: "Attacks resisted",
    cell: (m) => (
      <Score
        cases={attackCases(m)}
        source={{ model: m, batch: m.injection, column: "Attacks resisted" }}
        note={unmetNote(m)}
      />
    ),
  },
  {
    title: "Control",
    cell: (m) => (
      <span className={`font-extrabold ${m.controlRight ? "" : "text-down"}`}>
        {m.controlRight ? "right" : "missed"}
      </span>
    ),
  },
  {
    title: "Unsure",
    cell: (m) => (
      <span className="font-extrabold whitespace-nowrap">
        {m.unsure} of {m.runs}
      </span>
    ),
  },
  {
    title: "Seconds to diagnose",
    cell: (m) => (
      <div className="flex flex-col gap-1.5">
        <span className="font-extrabold tabular-nums">{m.secondsToDiagnosis} s</span>
        {m.note && <span className="text-sm text-ink-2">{m.note}</span>}
      </div>
    ),
  },
  {
    title: "Cost a run",
    cell: (m) => <span className="font-extrabold tabular-nums">{formatCost(m.costPerRun)}</span>,
  },
];

function ModelName({ model }: { model: ModelResult }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="font-extrabold whitespace-nowrap text-ink">{model.name}</span>
      <span className="text-sm text-ink-2">{model.route}</span>
    </div>
  );
}

/** The round's results: a table where it fits (1024 px), one card per model below that. */
export function Results({ models }: { models: ModelResult[] }) {
  return (
    <>
      <div className="hidden overflow-x-auto rounded-card border border-line bg-surface lg:block">
        <table className="w-full text-left align-top">
          <thead>
            <tr className="border-b border-line text-sm text-ink-2">
              <th scope="col" className="px-5 py-4 font-bold">
                Model
              </th>
              {COLUMNS.map((column) => (
                <th key={column.title} scope="col" className="px-3 py-4 font-bold">
                  {column.title}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {models.map((model) => (
              <tr key={model.name} className="border-b border-line last:border-0">
                <th scope="row" className="px-5 py-5 align-top font-normal">
                  <ModelName model={model} />
                </th>
                {COLUMNS.map((column) => (
                  <td key={column.title} className="px-3 py-5 align-top">
                    {column.cell(model)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="grid gap-4 md:grid-cols-2 lg:hidden">
        {models.map((model) => (
          <li key={model.name} className="rounded-card border border-line bg-surface p-5">
            <ModelName model={model} />
            <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-4">
              {COLUMNS.map((column) => (
                <div key={column.title} className="flex min-w-0 flex-col gap-1">
                  <dt className="text-sm text-ink-2">{column.title}</dt>
                  <dd>{column.cell(model)}</dd>
                </div>
              ))}
            </dl>
          </li>
        ))}
      </ul>
    </>
  );
}

/** What the dots mean, under the results. */
export function Legend() {
  return (
    <DotLegend
      pass="One case, right"
      fail="One case, missed"
      unmet="An attack the model never met"
    />
  );
}
