import { DecisionBar } from "@/components/incident/decision-bar";
import type { ApprovalView, Proposal } from "@/lib/api-types";
import { CHANNEL } from "@/lib/format";

const STATUS_WORD: Record<ApprovalView["status"], string> = {
  pending: "waiting",
  approved: "approved",
  rejected: "rejected",
  instructed: "answered with an instruction",
};

function samePlan(a: Proposal, b: Proposal): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

function Plan({ proposal }: { proposal: Proposal }) {
  const { containers, urls } = proposal.verification;
  return (
    <div className="flex flex-col gap-3 text-sm">
      {proposal.kind === "no_action" ? (
        <p className="font-medium">No action proposed.</p>
      ) : (
        <ol aria-label="Steps" className="flex flex-col gap-1.5">
          {proposal.steps.map((step, index) => (
            <li key={index} className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <span className="text-xs text-muted-foreground">{index + 1}.</span>
              <code className="font-mono font-medium">{step.action}</code>
              <code className="font-mono">{step.target}</code>
              <span className="text-xs text-muted-foreground">on {step.host_id}</span>
              {Object.entries(step.params).map(([key, value]) => (
                <code key={key} className="font-mono text-xs break-all text-muted-foreground">
                  {key}={value}
                </code>
              ))}
            </li>
          ))}
        </ol>
      )}
      <p>{proposal.reason}</p>
      {proposal.kind === "act" && containers.length + urls.length > 0 && (
        <p className="text-xs text-muted-foreground">
          Afterwards Bleep checks{" "}
          {[...containers, ...urls].map((item, index) => (
            <span key={item}>
              {index > 0 && ", "}
              <code className="font-mono break-all">{item}</code>
            </span>
          ))}
          .
        </p>
      )}
      {proposal.human_instructions && (
        <p className="text-xs text-muted-foreground">
          Planned with your instruction: “{proposal.human_instructions}”
        </p>
      )}
    </div>
  );
}

/**
 * The plan and what was answered. While an approval waits, the card shows the plan that approval
 * put to the human (not a newer one the run may hold) and the decision bar bound to it. A decided
 * approval for an earlier plan, such as an instruction that led to this one, gets one line.
 */
export function ProposalCard({
  incidentId,
  proposal,
  approval,
  onDecided,
}: {
  incidentId: string;
  proposal: Proposal | null;
  approval: ApprovalView | null;
  onDecided: () => void;
}) {
  const shown =
    approval?.status === "pending" ? approval.proposal : (proposal ?? approval?.proposal);
  if (!shown) return null;
  const forShown = approval !== null && samePlan(approval.proposal, shown);
  return (
    <div className="flex flex-col gap-4">
      <Plan proposal={shown} />
      {approval && forShown && (
        <DecisionBar
          key={approval.id}
          incidentId={incidentId}
          approval={approval}
          onDecided={onDecided}
        />
      )}
      {approval && !forShown && approval.decision && (
        <p className="text-xs text-muted-foreground">
          Earlier plan {STATUS_WORD[approval.status]} on {CHANNEL[approval.decision.via]}
          {approval.decision.text ? `: “${approval.decision.text}”` : ""}
        </p>
      )}
    </div>
  );
}
