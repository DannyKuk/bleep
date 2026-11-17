import type { Outcome } from "~/lib/benchmarks/extract";

const DOT: Record<Outcome, string> = {
  pass: "bg-ok",
  fail: "bg-down",
  unmet: "border-[1.5px] border-dashed border-stop",
};

/** A stronger model's run: the same dot inside a ring, so it reads apart from the model under test. */
const STRONGER = "ring-[1.5px] ring-ink ring-offset-2 ring-offset-surface";

/** One run's verdict as a dot: filled green or red, a dashed ring when the run tested nothing. */
export function OutcomeDot({
  outcome,
  stronger = false,
}: {
  outcome: Outcome;
  stronger?: boolean;
}) {
  return (
    <span
      aria-hidden
      className={`block size-2.5 shrink-0 rounded-full ${DOT[outcome]} ${stronger ? STRONGER : ""}`}
    />
  );
}

/** What the dots mean, with the words a suite uses for a run that tested nothing. */
export function DotLegend({ unmet, pass = "Right", fail = "Missed", stronger }: LegendWords) {
  const item = (outcome: Outcome, text: string, ringed = false) => (
    <li className="flex items-center gap-2">
      <OutcomeDot outcome={outcome} stronger={ringed} />
      {text}
    </li>
  );
  return (
    <ul className="flex flex-wrap gap-x-6 gap-y-2 text-sm text-ink-2">
      {item("pass", pass)}
      {item("fail", fail)}
      {unmet && item("unmet", unmet)}
      {stronger && item("pass", stronger, true)}
    </ul>
  );
}

export type LegendWords = { pass?: string; fail?: string; unmet?: string; stronger?: string };
