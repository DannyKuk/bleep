import type { Outcome } from "~/lib/benchmarks/extract";

const DOT: Record<Outcome, string> = {
  pass: "bg-ok",
  fail: "bg-down",
  unmet: "border-[1.5px] border-dashed border-stop",
};

/** One run's verdict as a dot: filled green or red, a dashed ring when the run tested nothing. */
export function OutcomeDot({ outcome }: { outcome: Outcome }) {
  return <span aria-hidden className={`block size-2.5 shrink-0 rounded-full ${DOT[outcome]}`} />;
}

/** What the dots mean, with the words a suite uses for a run that tested nothing. */
export function DotLegend({ unmet, pass = "Right", fail = "Missed" }: LegendWords) {
  const item = (outcome: Outcome, text: string) => (
    <li className="flex items-center gap-2">
      <OutcomeDot outcome={outcome} />
      {text}
    </li>
  );
  return (
    <ul className="flex flex-wrap gap-x-6 gap-y-2 text-sm text-ink-2">
      {item("pass", pass)}
      {item("fail", fail)}
      {unmet && item("unmet", unmet)}
    </ul>
  );
}

export type LegendWords = { pass?: string; fail?: string; unmet?: string };
