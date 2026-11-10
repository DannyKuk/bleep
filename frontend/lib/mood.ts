import type { HostView, Incident, IncidentStatus, Outcome, Subject } from "@/lib/api-types";

/** Bleep's moods, in the order that decides when several apply (first wins), plus resting. */
export const MOODS = ["needs_you", "not_sure", "working", "all_good", "resting"] as const;
export type Mood = (typeof MOODS)[number];

/** The header's colour of how things stand: red needs a human, amber Bleep is busy, green fine. */
export type OverallState = "red" | "amber" | "green";

/** How long a failed run keeps the overall state red. Nothing reopens a failed incident, so
 * counting it for ever would leave the header red for good. */
export const FAILED_NEEDS_YOU_MS = 24 * 60 * 60 * 1000;

const WORKING: ReadonlySet<IncidentStatus> = new Set([
  "open",
  "investigating",
  "executing",
  "verifying",
  "paused",
  "waiting",
]);

/** The incident page's mood: `unsure` is the diagnosis's flag, which only matters while the plan
 * waits for the human. */
export function incidentMood(status: IncidentStatus, unsure = false): Mood {
  if (status === "awaiting_approval") return unsure ? "not_sure" : "needs_you";
  if (status === "failed") return "needs_you";
  if (status === "resolved") return "all_good";
  if (status === "closed") return "resting";
  return "working";
}

/** The report's mood: a fix nobody confirmed needs the human; a run that ended quietly rests. A
 * look whose checks found the service healthy is good news too. */
export function reportMood(outcome: Outcome): Mood {
  if (outcome === "resolved" || outcome === "healthy") return "all_good";
  if (outcome === "unverified" || outcome === "budget") return "needs_you";
  return "resting";
}

/** A failed run that ended within `FAILED_NEEDS_YOU_MS`: it still needs the human. */
export function failedRecently(incident: Incident, now: number): boolean {
  if (incident.status !== "failed") return false;
  const ended = Date.parse(incident.closed_at ?? incident.opened_at);
  return now - ended < FAILED_NEEDS_YOU_MS;
}

type QuietHost = Pick<HostView, "id" | "reachable" | "may_sleep" | "quiet" | "quiet_service_ids">;

/** A host that may sleep and does not answer: it is asleep, which is no trouble. */
export function isAsleep(host: Pick<HostView, "reachable" | "may_sleep">): boolean {
  return !host.reachable && host.may_sleep;
}

/** Whether the subject's service, or its whole host, is quiet: Bleep leaves it alone. */
export function isQuietSubject(
  subject: Subject,
  hosts: readonly Pick<HostView, "id" | "quiet" | "quiet_service_ids">[],
): boolean {
  const host = hosts.find((candidate) => candidate.id === subject.host_id);
  if (!host) return false;
  if (host.quiet) return true;
  return subject.kind === "service" && host.quiet_service_ids.includes(subject.service_id ?? "");
}

/**
 * The overview's mood from the incidents and the hosts. `unsureIds` holds the waiting incidents
 * whose diagnosis is unsure (read from their detail); without it every waiting plan needs you.
 * A quiet subject's failed run and a quiet host's unreachability do not count.
 */
export function overallMood(
  incidents: readonly Incident[],
  hosts: readonly QuietHost[],
  unsureIds: ReadonlySet<string> = new Set(),
  now: number = Date.now(),
): Mood {
  const waiting = incidents.filter((incident) => incident.status === "awaiting_approval");
  const failedForYou = (incident: Incident) =>
    failedRecently(incident, now) && !isQuietSubject(incident.subject, hosts);
  if (incidents.some(failedForYou)) return "needs_you";
  if (waiting.some((incident) => !unsureIds.has(incident.id))) return "needs_you";
  if (waiting.length > 0) return "not_sure";
  if (incidents.some((incident) => WORKING.has(incident.status))) return "working";
  if (hosts.some((host) => !host.reachable && !host.quiet && !isAsleep(host))) return "working";
  return "all_good";
}

/** The header's three-colour state; it has no Not sure, since a waiting plan is red either way. */
export function overallState(
  incidents: readonly Incident[],
  hosts: readonly QuietHost[],
  now: number = Date.now(),
): OverallState {
  const mood = overallMood(incidents, hosts, new Set(), now);
  if (mood === "needs_you") return "red";
  if (mood === "working") return "amber";
  return "green";
}

/** The mood the head-only Bleep shows for a state, so its eyes take the state's colour. */
export function stateMood(state: OverallState): Mood {
  if (state === "red") return "needs_you";
  if (state === "amber") return "working";
  return "all_good";
}
