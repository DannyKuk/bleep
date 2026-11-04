import type { IncidentDetail, Outcome, Proposal, Subject } from "@/lib/api-types";

// What Propose said before `refused` existed, so an older refused plan still reads as refused
// (`REFUSED` in backend/src/bleep/graph/nodes/propose.py).
const REFUSED_REASON = "Bleep's own checks refused the proposed plan twice, so nothing will run.";

/** A plan Bleep's own checks refused: it can be instructed or let go, never approved. */
export function isRefused(proposal: Proposal): boolean {
  return proposal.refused === true || proposal.reason === REFUSED_REASON;
}

/**
 * The subject as the headline names it: a Compose project's or a container's own name, "Host
 * demo" for a host. A service id from another source (a Coolify uuid) is no name to say aloud.
 */
export function subjectName(subject: Subject): string {
  if (subject.kind === "host" || !subject.service_id) return `Host ${subject.host_id}`;
  const key = subject.service_id.slice(subject.service_id.indexOf("/") + 1);
  const colon = key.indexOf(":");
  const source = key.slice(0, colon);
  const name = key.slice(colon + 1);
  return colon > 0 && name && (source === "compose" || source === "container")
    ? name
    : "This service";
}

const CLOSED: Record<Outcome, string> = {
  resolved: "Fixed and checked.",
  rejected: "Okay, I left it as it is.",
  no_action: "I cannot fix this one. Here is what to do.",
  unverified: "I ran the plan, but could not confirm the fix.",
  budget: "I stopped: this run used up its budget.",
  replan_limit: "I found no plan I may run.",
};

/**
 * The incident page's headline for the moment, in Bleep's voice. It says "fixed" only for a
 * resolved incident, which the run reaches only once its checks passed.
 */
export function headline({
  incident,
  approval,
  diagnosis,
  outcome,
}: Pick<IncidentDetail, "incident" | "approval" | "diagnosis" | "outcome">): string {
  switch (incident.status) {
    case "open":
    case "investigating":
      return "I am looking into it.";
    case "paused":
      return "Paused for a moment. I will try again.";
    case "awaiting_approval": {
      if (approval && isRefused(approval.proposal)) return "I found no plan I may run. What now?";
      const name = subjectName(incident.subject);
      return diagnosis?.unsure
        ? `${name} is in trouble. I have a plan, but I am not sure.`
        : `${name} is in trouble. Can I fix it?`;
    }
    case "executing":
      return "On it.";
    case "verifying":
      return "Almost there.";
    case "resolved":
      return "Fixed and checked.";
    case "failed":
      return "I got stuck. This needs a look.";
    case "closed":
      return outcome ? CLOSED[outcome] : "Closed.";
  }
}
