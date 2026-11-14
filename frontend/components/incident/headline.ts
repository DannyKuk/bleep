import type { Incident, IncidentDetail, Proposal, Subject } from "@/lib/api-types";
import { showInvisible } from "@/lib/format";
import type { Messages } from "@/lib/i18n";

// Longer names are cut, so a label set on a host cannot fill the headline.
const NAME_MAX = 40;

// What Propose said before `refused` existed, so an older refused plan still reads as refused
// (`REFUSED` in backend/src/bleep/graph/nodes/propose.py).
const REFUSED_REASON = "Bleep's own checks refused the proposed plan twice, so nothing will run.";

/** A plan Bleep's own checks refused: it can be instructed or let go, never approved. */
export function isRefused(proposal: Proposal): boolean {
  return proposal.refused === true || proposal.reason === REFUSED_REASON;
}

/** A plan of no action put to the human all the same, refused or one whose fix Bleep could run
 * (#365): it waits for an instruction or "Let it go", never a yes. */
export function isInstructOnly(proposal: Proposal): boolean {
  return isRefused(proposal) || (proposal.kind === "no_action" && proposal.fix_runnable === true);
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
export function subjectName(subject: Subject, t: Messages): SubjectName {
  if (subject.kind === "host" || !subject.service_id) {
    return { lead: t.headline.host, name: shown(subject.host_id) };
  }
  const key = subject.service_id.slice(subject.service_id.indexOf("/") + 1);
  const colon = key.indexOf(":");
  const source = key.slice(0, colon);
  const name = key.slice(colon + 1);
  return colon > 0 && name && (source === "compose" || source === "container")
    ? { lead: "", name: shown(name) }
    : { lead: t.headline.thisService, name: "" };
}

/**
 * The service a look on request found healthy (#421): its own name where the id has one, else the
 * name its short name gives ("dkwebsite #3" names dkwebsite, a Coolify app's uuid does not), else
 * "This service".
 */
export function lookedAt(
  incident: Pick<Incident, "subject" | "short_name">,
  t: Messages,
): SubjectName {
  const named = subjectName(incident.subject, t);
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

/**
 * The incident page's headline for the moment, in Bleep's voice. It says "fixed" only for a
 * resolved incident, which the run reaches only once its checks passed. A healthy look's text,
 * and the trouble line, follow the subject's name.
 */
export function headline(
  {
    incident,
    approval,
    diagnosis,
    outcome,
  }: Pick<IncidentDetail, "incident" | "approval" | "diagnosis" | "outcome">,
  t: Messages,
): Headline {
  const say = (text: string): Headline => ({ text });
  const words = t.headline;
  switch (incident.status) {
    case "open":
    case "investigating":
      return say(words.looking);
    case "paused":
      return say(words.paused);
    case "waiting":
      return say(words.waiting);
    case "awaiting_approval": {
      if (approval && isRefused(approval.proposal)) return say(words.noPlanWhatNow);
      const text = diagnosis?.unsure ? words.troubleUnsure : words.trouble;
      return { ...subjectName(incident.subject, t), text };
    }
    case "executing":
      return say(words.executing);
    case "verifying":
      return say(words.verifying);
    case "resolved":
      return say(words.resolved);
    case "failed":
      return say(words.failed);
    case "closed":
      // A healthy look and a service back on its own (#571) both follow the subject's name.
      if (outcome === "healthy" || outcome === "recovered") {
        return { ...lookedAt(incident, t), text: words.ended[outcome] };
      }
      return say(outcome ? words.ended[outcome] : words.closed);
  }
}
