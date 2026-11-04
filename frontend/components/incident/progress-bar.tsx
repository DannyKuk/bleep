import { X } from "lucide-react";

import type { ProgressStep, StepState } from "@/components/incident/progress";
import { cn } from "@/lib/utils";

const BAR: Record<StepState, string> = {
  done: "bg-ok",
  current: "bg-accent",
  todo: "bg-line",
  stopped: "bg-down",
};

const LABEL: Record<StepState, string> = {
  done: "font-semibold text-ink",
  current: "font-extrabold text-ink",
  todo: "font-semibold text-ink-2",
  stopped: "font-extrabold text-down",
};

// Said to a screen reader after the label; on screen, weight and the cross carry the same.
const WORD: Record<StepState, string> = {
  done: "done",
  current: "now",
  todo: "not done",
  stopped: "stopped here",
};

/** The run's eight steps as segments: done green, the current one accent and bold, to do grey,
 * and the step a failed run stopped at red with a cross. */
export function ProgressBar({ steps }: { steps: ProgressStep[] }) {
  return (
    <ol aria-label="Progress" className="grid grid-cols-4 gap-x-1.5 gap-y-3 sm:grid-cols-8">
      {steps.map(({ label, state }) => (
        <li
          key={label}
          aria-current={state === "current" ? "step" : undefined}
          data-state={state}
          className={cn("flex min-w-0 flex-col gap-1.5 text-[13px]", LABEL[state])}
        >
          <span aria-hidden className={cn("h-2 rounded-full", BAR[state])} />
          <span className="flex items-center gap-1">
            {state === "stopped" && <X aria-hidden className="size-3.5 shrink-0" strokeWidth={3} />}
            {label}
            <span className="sr-only">, {WORD[state]}</span>
          </span>
        </li>
      ))}
    </ol>
  );
}
