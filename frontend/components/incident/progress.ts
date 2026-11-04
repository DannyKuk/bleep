import type { IncidentEvent, IncidentStatus } from "@/lib/api-types";

/** The run's eight steps as the progress bar names them, each with its graph node. */
export const PROGRESS_STEPS = [
  { node: "gather", label: "Gather" },
  { node: "investigate", label: "Investigate" },
  { node: "diagnose", label: "Diagnose" },
  { node: "propose", label: "Plan" },
  { node: "approval", label: "Your yes" },
  { node: "execute", label: "Fix" },
  { node: "verify", label: "Check" },
  { node: "report", label: "Report" },
] as const;

export type StepState = "done" | "current" | "todo" | "stopped";

export interface ProgressStep {
  label: string;
  state: StepState;
}

const INDEX = new Map<string, number>(PROGRESS_STEPS.map(({ node }, index) => [node, index]));
const REPORT = INDEX.get("report")!;
// Where a run that has published nothing yet (or whose history is still loading) must be.
const BY_STATUS: Partial<Record<IncidentStatus, number>> = {
  awaiting_approval: INDEX.get("approval"),
  executing: INDEX.get("execute"),
  verifying: INDEX.get("verify"),
};

/**
 * The progress bar from the node events the timeline reads. A step is done once its node
 * finished; a node that starts again (a check that failed sends the run back to Investigate)
 * clears the steps from there on, so a step the run has not reached again is never shown done.
 * Steps that never ran stay to do, a failed run marks the step it stopped at, and a closed or
 * resolved incident has its report done.
 */
export function progressSteps(events: IncidentEvent[], status: IncidentStatus): ProgressStep[] {
  const done = new Set<number>();
  let current: number | null = null;
  let last: number | null = null;
  for (const { kind, payload } of events) {
    if (kind !== "node_started" && kind !== "node_finished") continue;
    const index = INDEX.get(String(payload.node ?? ""));
    if (index === undefined) continue;
    if (kind === "node_started") {
      for (const step of done) if (step >= index) done.delete(step);
      current = index;
      last = index;
    } else {
      done.add(index);
      if (current === index) current = null;
    }
  }

  let stopped: number | null = null;
  if (status === "resolved" || status === "closed") {
    current = null;
    done.add(REPORT);
  } else if (status === "failed") {
    stopped = current ?? last ?? 0;
    current = null;
    done.delete(stopped);
  } else if (current === null) {
    current = BY_STATUS[status] ?? null;
  }

  return PROGRESS_STEPS.map(({ label }, index) => ({
    label,
    state:
      index === stopped
        ? "stopped"
        : index === current
          ? "current"
          : done.has(index)
            ? "done"
            : "todo",
  }));
}
