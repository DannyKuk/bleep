"use client";

import { type KeyboardEvent, type ReactNode, useEffect, useRef } from "react";

import type { CaseRuns, Outcome } from "~/lib/benchmarks/extract";

import { OutcomeDot } from "./dot";
import { type RunRef, useOpenRun } from "./run-dialog";

const STEPS: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };

/** Its dots are one stop for Tab; the arrow keys, Home and End move between them. */
export function RovingGroup({ children, className }: { children: ReactNode; className?: string }) {
  const group = useRef<HTMLDivElement>(null);
  const dots = () => [...(group.current?.querySelectorAll<HTMLElement>("[data-dot]") ?? [])];

  useEffect(() => {
    dots().forEach((dot, i) => (dot.tabIndex = i === 0 ? 0 : -1));
  }, []);

  function onFocus(event: React.FocusEvent) {
    const all = dots();
    if (!all.includes(event.target as HTMLElement)) return;
    for (const dot of all) dot.tabIndex = dot === event.target ? 0 : -1;
  }

  function onKeyDown(event: KeyboardEvent) {
    const all = dots();
    const at = all.indexOf(event.target as HTMLElement);
    if (at < 0) return;
    const to =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? all.length - 1
          : event.key in STEPS
            ? Math.min(all.length - 1, Math.max(0, at + STEPS[event.key]))
            : null;
    if (to === null) return;
    event.preventDefault();
    all[to].focus();
  }

  return (
    <div ref={group} role="group" className={className} onFocus={onFocus} onKeyDown={onKeyDown}>
      {children}
    </div>
  );
}

/** One run as a button that opens its detail. */
export function DotButton({ run, label }: { run: RunRef; label: string }) {
  const open = useOpenRun();
  return (
    <button
      type="button"
      data-dot
      aria-label={label}
      title={label}
      aria-haspopup="dialog"
      onClick={() => open(run)}
      className="-m-1.5 flex size-6 items-center justify-center rounded-full outline-offset-0 hover:bg-soft focus-visible:outline-2 focus-visible:outline-accent"
    >
      <OutcomeDot outcome={run.outcome} />
    </button>
  );
}

export type GridWords = {
  /** How a run's verdict reads, as the suite counts it. */
  verdicts: Record<Outcome, string>;
  /** A group's heading, such as "Through logs", or null for a suite without groups. */
  groups: Record<string, string>;
};

type SuiteGridProps = {
  suite: RunRef["set"];
  suiteName: string;
  model: string;
  cases: CaseRuns[];
  words: GridWords;
};

/** Every case of a suite with a dot for each of its runs. */
export function SuiteGrid({ suite, suiteName, model, cases, words }: SuiteGridProps) {
  // Grouped by hand: Map.groupBy is missing from Safari before 17.4, and this runs in the browser.
  const groups = new Map<string, CaseRuns[]>();
  for (const c of cases) groups.set(c.group ?? "", [...(groups.get(c.group ?? "") ?? []), c]);
  return (
    <RovingGroup className="flex flex-col gap-6">
      {[...groups.entries()].map(([group, inGroup]) => (
        <div key={group}>
          {group && (
            <h3 className="mb-2 text-sm font-extrabold text-ink-2">
              {words.groups[group] ?? group} <span className="font-normal">({inGroup.length})</span>
            </h3>
          )}
          <ul className="grid gap-x-8 sm:grid-cols-2 lg:grid-cols-3">
            {inGroup.map((c) => (
              <li
                key={c.name}
                className="flex min-h-8 items-center justify-between gap-3 border-b border-line py-1"
              >
                <span className="min-w-0 truncate font-mono text-[13px]" title={c.name}>
                  {c.name}
                </span>
                <span className="flex shrink-0 gap-3 pr-1.5">
                  {c.runs.map((outcome, i) => {
                    const run = i + 1;
                    const verdict = words.verdicts[outcome];
                    return (
                      <DotButton
                        key={run}
                        label={`${c.name}, run ${run}: ${verdict}`}
                        run={{
                          set: suite,
                          key: `${c.name}/${run}`,
                          title: c.name,
                          subtitle: `${suiteName}, run ${run} of ${c.runs.length}, ${model}`,
                          outcome,
                          verdict,
                        }}
                      />
                    );
                  })}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </RovingGroup>
  );
}
