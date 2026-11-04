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

/**
 * `done` went well, `current` runs now, `todo` was not reached, `failed` ran and went wrong (a
 * failed step of the plan, checks that never passed), `stopped` is where a failed run ended, and
 * `declined` is the human's no on "Your yes".
 */
export type StepState = "done" | "current" | "todo" | "failed" | "stopped" | "declined";

export interface ProgressStep {
  label: string;
  state: StepState;
}

const INDEX = new Map<string, number>(PROGRESS_STEPS.map(({ node }, index) => [node, index]));
const REPORT = INDEX.get("report")!;
const APPROVAL = INDEX.get("approval")!;
const EXECUTE = INDEX.get("execute")!;
const VERIFY = INDEX.get("verify")!;
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
 * Fix is failed when a step of the plan failed, and Check is failed unless its last check passed,
 * so a fix nobody confirmed never looks like one that was; a no on Your yes shows as declined.
 * Steps that never ran stay to do, a failed run marks the step it stopped at, and a closed or
 * resolved incident has its report done.
 */
export function progressSteps(events: IncidentEvent[], status: IncidentStatus): ProgressStep[] {
  const ended = new Map<number, StepState>();
  let current: number | null = null;
  let last: number | null = null;
  let stepFailed = false;
  let checkPassed = false;
  let declined = false;
  for (const { kind, payload } of events) {
    if (kind === "action" && payload.status === "failed") stepFailed = true;
    if (kind === "verification") checkPassed = payload.ok === true;
    if (kind === "decision") declined = payload.kind === "reject";
    if (kind !== "node_started" && kind !== "node_finished") continue;
    const index = INDEX.get(String(payload.node ?? ""));
    if (index === undefined) continue;
    if (kind === "node_started") {
      for (const step of ended.keys()) if (step >= index) ended.delete(step);
      if (index === EXECUTE) stepFailed = false;
      if (index === VERIFY) checkPassed = false;
      current = index;
      last = index;
    } else {
      ended.set(index, endState(index, { stepFailed, checkPassed, declined }));
      if (current === index) current = null;
    }
  }

  let stopped: number | null = null;
  if (status === "resolved" || status === "closed") {
    current = null;
    if (!ended.has(REPORT)) ended.set(REPORT, "done");
  } else if (status === "failed") {
    // With no node event yet (the history still loading) there is no step to mark.
    stopped = current ?? last;
    current = null;
    if (stopped !== null) ended.delete(stopped);
  } else if (current === null) {
    current = BY_STATUS[status] ?? null;
  }

  return PROGRESS_STEPS.map(({ label }, index) => ({
    label,
    state:
      index === stopped ? "stopped" : index === current ? "current" : (ended.get(index) ?? "todo"),
  }));
}

function endState(
  index: number,
  run: { stepFailed: boolean; checkPassed: boolean; declined: boolean },
): StepState {
  if (index === EXECUTE && run.stepFailed) return "failed";
  if (index === VERIFY && !run.checkPassed) return "failed";
  if (index === APPROVAL && run.declined) return "declined";
  return "done";
}
