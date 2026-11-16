import {
  type Attack,
  type Fault,
  type ModelResult,
  count,
  formatCost,
} from "~/lib/benchmarks/round";

type Outcome = "right" | "wrong" | "unmet";

/** One dot per case run, so a reader sees which case a model missed, not only how many. */
function Dots({ cases }: { cases: [string, Outcome][] }) {
  return (
    <span aria-hidden className="flex gap-1">
      {cases.map(([name, outcome]) => (
        <span
          key={name}
          title={`${name}: ${outcome === "right" ? "right" : outcome === "wrong" ? "missed" : "never reached the model"}`}
          className={`size-2.5 rounded-full ${
            outcome === "right"
              ? "bg-ok"
              : outcome === "wrong"
                ? "bg-down"
                : "border-[1.5px] border-dashed border-stop"
          }`}
        />
      ))}
    </span>
  );
}

function Score({ cases, note }: { cases: [string, Outcome][]; note?: string }) {
  const right = cases.filter(([, outcome]) => outcome !== "wrong").length;
  const missed = cases.filter(([, outcome]) => outcome === "wrong").map(([name]) => name);
  return (
    <div className="flex flex-col gap-1.5">
      <span className="font-extrabold whitespace-nowrap">
        {right} of {cases.length}
      </span>
      <Dots cases={cases} />
      {missed.length > 0 && (
        <span className="text-sm whitespace-nowrap text-down">missed {missed.join(", ")}</span>
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
  { title: "Cause found", cell: (m) => <Score cases={faultCases(m, (f) => f.rootCause)} /> },
  { title: "Exact plan", cell: (m) => <Score cases={faultCases(m, (f) => f.exactPlan)} /> },
  { title: "Unneeded actions", cell: unnecessary },
  {
    title: "Attacks resisted",
    cell: (m) => <Score cases={attackCases(m)} note={unmetNote(m)} />,
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

/** The round's results: a table from medium width, one card per model below it. */
export function Results({ models }: { models: ModelResult[] }) {
  return (
    <>
      <div className="hidden overflow-hidden rounded-card border border-line bg-surface md:block">
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
      <ul className="grid gap-4 md:hidden">
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
  const item = (dot: string, text: string) => (
    <li className="flex items-center gap-2">
      <span aria-hidden className={`size-2.5 rounded-full ${dot}`} />
      {text}
    </li>
  );
  return (
    <ul className="flex flex-wrap gap-x-6 gap-y-2 text-sm text-ink-2">
      {item("bg-ok", "One case, right")}
      {item("bg-down", "One case, missed")}
      {item("border-[1.5px] border-dashed border-stop", "An attack the model never met")}
    </ul>
  );
}
