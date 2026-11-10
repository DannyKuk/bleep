import type { Incident, IncidentDetail, Outcome, Proposal, Subject } from "@/lib/api-types";
import { showInvisible } from "@/lib/format";

// Longer names are cut, so a label set on a host cannot fill the headline.
const NAME_MAX = 40;

// What Propose said before `refused` existed, so an older refused plan still reads as refused
// (`REFUSED` in backend/src/bleep/graph/nodes/propose.py).
const REFUSED_REASON = "Bleep's own checks refused the proposed plan twice, so nothing will run.";

/** A plan Bleep's own checks refused: it can be instructed or let go, never approved. */
export function isRefused(proposal: Proposal): boolean {
  return proposal.refused === true || proposal.reason === REFUSED_REASON;
}

/**
 * How a headline names the subject: `lead` is Bleep's words, `name` is data (a Compose project's
 * or a container's own name, or a host's id) to show in mono. A service id from another source (a
 * Coolify uuid) is no name to say aloud.
 */
export interface SubjectName {
  lead: string;
  name: string;
}

/** The subject for a headline. The name comes from the host (a Compose label anyone starting a
 * container sets), so characters that draw nothing are marked and a long one is cut. */
export function subjectName(subject: Subject): SubjectName {
  if (subject.kind === "host" || !subject.service_id) {
    return { lead: "Host ", name: shown(subject.host_id) };
  }
  const key = subject.service_id.slice(subject.service_id.indexOf("/") + 1);
  const colon = key.indexOf(":");
  const source = key.slice(0, colon);
  const name = key.slice(colon + 1);
  return colon > 0 && name && (source === "compose" || source === "container")
    ? { lead: "", name: shown(name) }
    : { lead: "This service", name: "" };
}

/**
 * The service a look on request found healthy (#421): its own name where the id has one, else the
 * name its short name gives ("dkwebsite #3" names dkwebsite, a Coolify app's uuid does not), else
 * "This service".
 */
export function lookedAt(incident: Pick<Incident, "subject" | "short_name">): SubjectName {
  const named = subjectName(incident.subject);
  const short = incident.short_name?.replace(/ #\d+$/, "") ?? "";
  return named.name || !short ? named : { lead: "", name: shown(short) };
}

function shown(name: string): string {
  const visible = showInvisible(name);
  return visible.length > NAME_MAX ? `${visible.slice(0, NAME_MAX - 1)}…` : visible;
}

/** A headline: Bleep's words, with the subject's name (when it has one) as data before them. */
export interface Headline extends Partial<SubjectName> {
  text: string;
}

/** The headline as one string, the way a screen reader reads it. */
export function headlineText({ lead = "", name = "", text }: Headline): string {
  return `${lead}${name}${text}`;
}

// A healthy look's text follows the service's name (`lookedAt`).
const CLOSED: Record<Outcome, string> = {
  resolved: "Fixed and checked.",
  healthy: " looks healthy. Nothing to do.",
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
}: Pick<IncidentDetail, "incident" | "approval" | "diagnosis" | "outcome">): Headline {
  const say = (text: string): Headline => ({ text });
  switch (incident.status) {
    case "open":
    case "investigating":
      return say("I am looking into it.");
    case "paused":
      return say("Paused for a moment. I will try again.");
    case "waiting":
      return say("The host does not answer. I wait for it to come back.");
    case "awaiting_approval": {
      if (approval && isRefused(approval.proposal)) {
        return say("I found no plan I may run. What now?");
      }
      const text = diagnosis?.unsure
        ? " is in trouble. I have a plan, but I am not sure."
        : " is in trouble. Can I fix it?";
      return { ...subjectName(incident.subject), text };
    }
    case "executing":
      return say("On it.");
    case "verifying":
      return say("Almost there.");
    case "resolved":
      return say("Fixed and checked.");
    case "failed":
      return say("I got stuck. This needs a look.");
    case "closed":
      if (outcome === "healthy") return { ...lookedAt(incident), text: CLOSED.healthy };
      return say(outcome ? CLOSED[outcome] : "Closed.");
  }
}
