import { X } from "lucide-react";

import { LOOK } from "@/components/incident/colour-code";
import type { ProgressStep, StepState } from "@/components/incident/progress";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const BOLD: ReadonlySet<StepState> = new Set(["current", "failed", "stopped"]);

/** The run's eight steps as segments in the incident page's colour code (`LOOK`); the current
 * step and one that went wrong are bold, and a step that went wrong, where a failed run stopped
 * or a no on Your yes has a cross. Each says its state to a screen reader, so the colour is never
 * the only cue. */
export function ProgressBar({ steps }: { steps: ProgressStep[] }) {
  const t = useT();
  return (
    <ol aria-label={t.progress.label} className="grid grid-cols-4 gap-x-1.5 gap-y-3 sm:grid-cols-8">
      {steps.map(({ node, state }) => {
        const look = LOOK[state];
        return (
          <li
            key={node}
            aria-current={state === "current" ? "step" : undefined}
            data-state={state}
            className={cn(
              "flex min-w-0 flex-col gap-1.5 text-[13px] font-semibold text-ink",
              BOLD.has(state) && "font-extrabold",
              look.tone,
            )}
          >
            <span aria-hidden className={cn("h-2 rounded-full", look.fill)} />
            <span className="flex min-w-0 items-center gap-1 wrap-anywhere hyphens-auto">
              {look.icon === "cross" && (
                <X aria-hidden className="size-3.5 shrink-0" strokeWidth={3} />
              )}
              {t.progress.steps[node]}
              <span className="sr-only">, {t.marks[state]}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}
