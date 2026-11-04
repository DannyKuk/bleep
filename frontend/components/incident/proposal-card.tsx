import { Check, Loader2, Minus, X } from "lucide-react";
import { useId } from "react";

import { DecisionBar } from "@/components/incident/decision-bar";
import { isRefused } from "@/components/incident/headline";
import {
  type CheckView,
  type PlanRun,
  type StepRun,
  stepStatuses,
} from "@/components/incident/plan-run";
import type {
  Action,
  ApprovalView,
  IncidentDetail,
  IncidentStatus,
  PlanStep,
  Proposal,
} from "@/lib/api-types";
import { CHANNEL, showInvisible } from "@/lib/format";
import { cn } from "@/lib/utils";

const STATUS_WORD: Record<ApprovalView["status"], string> = {
  pending: "waiting",
  approved: "approved",
  rejected: "rejected",
  instructed: "answered with an instruction",
};

const VERB: Record<Action, string> = {
  "docker.restart": "Restart",
  "docker.stop": "Stop",
  "docker.start": "Start",
  "coolify.redeploy": "Redeploy",
  "coolify.set_env": "Set a variable on",
  "mcp.call": "Call a tool for",
};

function samePlan(a: Proposal, b: Proposal): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** The plan this card shows: while an approval waits, the plan that approval put to the human. */
function shownPlan(proposal: Proposal | null, approval: ApprovalView | null): Proposal | undefined {
  return approval?.status === "pending" ? approval.proposal : (proposal ?? approval?.proposal);
}

/** The approval the decision bar answers, when the card shows one: the approval's own plan. */
export function barApproval(
  proposal: Proposal | null,
  approval: ApprovalView | null,
): ApprovalView | null {
  const shown = shownPlan(proposal, approval);
  return approval && shown && samePlan(approval.proposal, shown) ? approval : null;
}

/** The plan card's title for where the run is. */
export function planTitle({
  incident,
  proposal,
  approval,
  outcome,
}: Pick<IncidentDetail, "incident" | "proposal" | "approval" | "outcome">): string {
  const shown = shownPlan(proposal, approval);
  if (!shown) return "My plan";
  if (isRefused(shown)) return "No plan I may run";
  if (shown.kind === "no_action") return "What I suggest";
  if (approval?.status === "pending") return "Here is my plan";
  if (incident.status === "executing" || incident.status === "verifying") return "Doing it now";
  if (incident.status === "resolved") return "What I did";
  if (outcome === "rejected") return "The plan you said no to";
  return "My plan";
}

const BADGE: Record<StepRun, { icon: typeof Check; word: string; className: string }> = {
  started: { icon: Loader2, word: "running", className: "text-warn motion-safe:animate-spin" },
  done: { icon: Check, word: "done", className: "text-ok" },
  failed: { icon: X, word: "failed", className: "text-down" },
  skipped: { icon: Minus, word: "skipped", className: "text-ink-2" },
};

/** The step's number until it runs, then a spinner, then a check (or a cross, or a dash). */
function StepBadge({ number, status }: { number: number; status?: StepRun }) {
  if (!status) {
    return (
      <span
        aria-hidden
        className="grid size-8 shrink-0 place-items-center rounded-full bg-surface text-sm font-extrabold text-accent"
      >
        {number}
      </span>
    );
  }
  const { icon: Icon, word, className } = BADGE[status];
  return (
    <span className="grid size-8 shrink-0 place-items-center rounded-full bg-surface">
      <Icon aria-hidden className={cn("size-4", className)} strokeWidth={2.75} />
      <span className="sr-only">
        Step {number}, {word}:
      </span>
    </span>
  );
}

function Step({ step, number, status }: { step: PlanStep; number: number; status?: StepRun }) {
  return (
    <li className="flex flex-wrap items-center gap-x-3.5 gap-y-1.5 rounded-row bg-soft px-4 py-3.5">
      <StepBadge number={number} status={status} />
      <span className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="text-[17px] font-extrabold">{VERB[step.action] ?? step.action}</span>
        <code className="font-mono text-[15px] break-all">{showInvisible(step.target)}</code>
      </span>
      <span className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1 text-sm text-ink-2">
        <code className="font-mono">{step.action}</code>
        <span>
          on <code className="font-mono">{step.host_id}</code>
        </span>
        {Object.entries(step.params).map(([key, value]) => (
          <code key={key} className="font-mono text-xs break-all">
            {showInvisible(`${key}=${value}`)}
          </code>
        ))}
      </span>
    </li>
  );
}

function CheckPill({ check }: { check: CheckView }) {
  const Icon = check.ok ? Check : X;
  return (
    <li
      className={cn(
        "inline-flex max-w-full items-center gap-1.5 rounded-full px-3 py-1.5 text-sm font-bold",
        check.ok ? "bg-ok-bg text-ok" : "bg-down-bg text-down",
      )}
    >
      <Icon aria-hidden className="size-3.5 shrink-0" strokeWidth={2.75} />
      <span className="sr-only">{check.ok ? "Passed:" : "Failed:"}</span>
      <code className="font-mono font-medium break-all">{check.target}</code>
      {check.detail && <span className="font-semibold">{check.detail}</span>}
    </li>
  );
}

function PendingPill({ target }: { target: string }) {
  return (
    <li className="inline-flex max-w-full items-center gap-1.5 rounded-full bg-soft px-3 py-1.5 text-sm font-bold">
      <Loader2 aria-hidden className="size-3.5 shrink-0 text-warn motion-safe:animate-spin" />
      <span className="sr-only">Checking:</span>
      <code className="font-mono font-medium break-all">{target}</code>
    </li>
  );
}

/** The verification checks as pills: the planned ones as "checking" until the first result. */
function Checks({
  planned,
  verification,
}: {
  planned: string[];
  verification: PlanRun["verification"];
}) {
  return (
    <div className="flex flex-col gap-2">
      <p className="text-sm font-bold text-ink-2">
        {verification
          ? `Check ${verification.attempt}: ${verification.ok ? "passed" : "failed"}`
          : "Checking"}
      </p>
      <ul aria-label="Checks" className="flex flex-wrap gap-2">
        {verification
          ? verification.checks.map((check, index) => <CheckPill key={index} check={check} />)
          : planned.map((target) => <PendingPill key={target} target={target} />)}
      </ul>
    </div>
  );
}

function Plan({
  proposal,
  run,
  status,
}: {
  proposal: Proposal;
  run?: PlanRun;
  status?: IncidentStatus;
}) {
  const planned = [...proposal.verification.containers, ...proposal.verification.urls];
  const statuses = run ? stepStatuses(proposal.steps, run) : [];
  const verification = run?.verification ?? null;
  const checking = verification !== null || (status === "verifying" && planned.length > 0);
  return (
    <div className="flex flex-col gap-4 text-[15px]">
      {proposal.kind === "no_action" ? (
        <p className="font-bold">No action proposed.</p>
      ) : (
        <ol aria-label="Steps" className="flex flex-col gap-2.5">
          {proposal.steps.map((step, index) => (
            <Step key={index} step={step} number={index + 1} status={statuses[index]} />
          ))}
        </ol>
      )}
      <p className="text-base leading-relaxed">{proposal.reason}</p>
      {proposal.kind === "act" && checking && (
        <Checks planned={planned} verification={verification} />
      )}
      {proposal.kind === "act" && !checking && planned.length > 0 && (
        <p className="text-ink-2">
          Afterwards I check{" "}
          {planned.map((item, index) => (
            <span key={item}>
              {index > 0 && ", "}
              <code className="font-mono break-all">{item}</code>
            </span>
          ))}
          .
        </p>
      )}
      {proposal.human_instructions && (
        <Advice
          title={isRefused(proposal) ? "Why nothing will run" : "What to do"}
          text={proposal.human_instructions}
        />
      )}
    </div>
  );
}

/**
 * `human_instructions` is Bleep's text to the human (the fix to make for a no-action plan, or why
 * a plan was refused), never the human's own words, which the timeline already shows.
 */
function Advice({ title, text }: { title: string; text: string }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex flex-col gap-1.5 rounded-row bg-soft px-4 py-3.5">
      <h3 id={id} className="font-extrabold">
        {title}
      </h3>
      <p className="break-words whitespace-pre-line">{text}</p>
    </section>
  );
}

/**
 * The plan and what was answered. While an approval waits, the card shows the plan that approval
 * put to the human (not a newer one the run may hold) and the decision bar bound to it. A decided
 * approval for an earlier plan, such as an instruction that led to this one, gets one line. With
 * `run`, each step shows its progress and the checks show as pills.
 */
export function ProposalCard({
  incidentId,
  proposal,
  approval,
  onDecided,
  run,
  status,
  pinned = false,
  nothingRanBefore = false,
}: {
  incidentId: string;
  proposal: Proposal | null;
  approval: ApprovalView | null;
  onDecided: () => void;
  run?: PlanRun;
  status?: IncidentStatus;
  /** Pin the decision bar to the bottom of a phone's screen. */
  pinned?: boolean;
  /** No step of any plan of this incident ran (see `DecisionBar`). */
  nothingRanBefore?: boolean;
}) {
  const shown = shownPlan(proposal, approval);
  if (!shown) return null;
  const forShown = barApproval(proposal, approval) !== null;
  return (
    <div className="flex flex-col gap-5">
      <Plan proposal={shown} run={run} status={status} />
      {approval && forShown && (
        <DecisionBar
          key={approval.id}
          incidentId={incidentId}
          approval={approval}
          onDecided={onDecided}
          pinned={pinned}
          status={status}
          nothingRanBefore={nothingRanBefore}
        />
      )}
      {approval && !forShown && approval.decision && (
        <p className="text-sm text-ink-2">
          Earlier plan {STATUS_WORD[approval.status]} on {CHANNEL[approval.decision.via]}
          {approval.decision.text ? `: “${approval.decision.text}”` : ""}
        </p>
      )}
    </div>
  );
}
