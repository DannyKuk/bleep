import type { IncidentEvent, PlanStep } from "@/lib/api-types";

export type StepRun = "started" | "done" | "failed" | "skipped";

/** One thing Verify looked at (`Check` in backend/src/bleep/schemas/graph.py). */
export interface CheckView {
  kind: string;
  target: string;
  ok: boolean;
  detail: string;
}

/** What the current plan has done so far: each step's latest status, and the latest check. */
export interface PlanRun {
  steps: { action: string; target: string; status: StepRun }[];
  verification: { ok: boolean; attempt: number; checks: CheckView[] } | null;
}

const STEP_RUNS = new Set<string>(["started", "done", "failed", "skipped"]);

function checksOf(payload: Record<string, unknown>): CheckView[] {
  if (!Array.isArray(payload.checks)) return [];
  return payload.checks
    .filter((check): check is Record<string, unknown> => !!check && typeof check === "object")
    .map((check) => ({
      kind: String(check.kind ?? ""),
      target: String(check.target ?? ""),
      ok: check.ok === true,
      detail: String(check.detail ?? ""),
    }));
}

/**
 * The `action` and `verification` events since the last `proposal`: a new plan starts from
 * nothing, so the steps of an earlier plan (one that failed its check and was planned again)
 * never show on the current one. Execute publishes `started` and then the end of each step.
 */
export function planRun(events: IncidentEvent[]): PlanRun {
  const run: PlanRun = { steps: [], verification: null };
  const since = events.findLastIndex((event) => event.kind === "proposal");
  for (const { kind, payload } of events.slice(since + 1)) {
    if (kind === "action" && STEP_RUNS.has(String(payload.status))) {
      const action = String(payload.action ?? "");
      const target = String(payload.target ?? "");
      const status = payload.status as StepRun;
      const open = run.steps.at(-1);
      if (open?.status === "started" && open.action === action && open.target === target) {
        open.status = status;
      } else {
        run.steps.push({ action, target, status });
      }
    } else if (kind === "verification") {
      run.verification = {
        ok: payload.ok === true,
        attempt: Number(payload.attempt ?? 1),
        checks: checksOf(payload),
      };
    }
  }
  return run;
}

/** Each plan step's status in `run`, matched in order by action and target; undefined until it
 * starts. */
export function stepStatuses(steps: PlanStep[], run: PlanRun): (StepRun | undefined)[] {
  const statuses: (StepRun | undefined)[] = steps.map(() => undefined);
  let next = 0;
  for (const { action, target, status } of run.steps) {
    const index = steps.findIndex(
      (step, at) => at >= next && step.action === action && step.target === target,
    );
    if (index === -1) continue;
    statuses[index] = status;
    next = index + 1;
  }
  return statuses;
}
