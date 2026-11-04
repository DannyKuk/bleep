import { X } from "lucide-react";

import type { ProgressStep, StepState } from "@/components/incident/progress";
import { cn } from "@/lib/utils";

const BAR: Record<StepState, string> = {
  done: "bg-ok",
  current: "bg-accent",
  todo: "bg-line",
  failed: "bg-down",
  stopped: "bg-down",
  declined: "bg-stop",
};

const LABEL: Record<StepState, string> = {
  done: "font-semibold text-ink",
  current: "font-extrabold text-ink",
  todo: "font-semibold text-ink-2",
  failed: "font-extrabold text-down",
  stopped: "font-extrabold text-down",
  declined: "font-semibold text-ink",
};

// Said to a screen reader after the label; on screen, weight and the cross carry the same.
const WORD: Record<StepState, string> = {
  done: "done",
  current: "now",
  todo: "not done",
  failed: "failed",
  stopped: "stopped here",
  declined: "you said no",
};

const MARKED: ReadonlySet<StepState> = new Set(["failed", "stopped", "declined"]);

/** The run's eight steps as segments: done green, the current one accent and bold, to do grey,
 * a step that went wrong or where a failed run stopped red with a cross, and a no on Your yes
 * grey with a cross. */
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
            {MARKED.has(state) && <X aria-hidden className="size-3.5 shrink-0" strokeWidth={3} />}
            {label}
            <span className="sr-only">, {WORD[state]}</span>
          </span>
        </li>
      ))}
    </ol>
  );
}
