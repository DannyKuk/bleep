import type {
  HostView,
  Incident,
  IncidentStatus,
  Outcome,
  ServiceView,
  Subject,
} from "@/lib/api-types";

/** Bleep's moods, in the order that decides when several apply (first wins), plus resting.
 * `watching` is the "hmm": something fails and the watcher has seen it, but it has opened no
 * incident yet, since it waits to see whether the failure holds. `pointing` is "that one is
 * down, have a look": a service shows down, but the watcher will open no incident for it. */
export const MOODS = [
  "needs_you",
  "not_sure",
  "working",
  "watching",
  "pointing",
  "all_good",
  "resting",
] as const;
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

type QuietHost = Pick<
  HostView,
  "id" | "reachable" | "may_sleep" | "quiet" | "quiet_service_ids" | "watching"
>;

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

/** A host Bleep cannot reach that is neither quiet nor asleep: it is trouble. */
export function isOutOfReach(host: Pick<HostView, "reachable" | "may_sleep" | "quiet">): boolean {
  return !host.reachable && !host.quiet && !isAsleep(host);
}

type WatchedService = Pick<ServiceView, "host_id" | "quiet" | "open_incident_id" | "watching">;
type MoodService = WatchedService & Pick<ServiceView, "health">;

/**
 * The services the watcher waits on before it opens an incident (their `watching`, from the
 * backend: a URL failing fewer times in a row than it needs, a death inside its grace window).
 * Only what the watcher reports counts, never a service that merely shows down: a paused or
 * created container, one dead before Bleep started or one still dead after its incident ended
 * opens nothing, so Bleep must not say it waits on it (`unwatchedDownServices` has those). A
 * quiet service, one with its incident open and one on a host Bleep cannot reach (the host's
 * trouble) are left out.
 */
export function watchedServices<S extends WatchedService>(
  services: readonly S[],
  hosts: readonly Pick<HostView, "id" | "reachable">[],
): S[] {
  const reached = new Set(hosts.filter((host) => host.reachable).map((host) => host.id));
  return services.filter(
    (service) =>
      service.watching.length > 0 &&
      service.quiet === null &&
      service.open_incident_id === null &&
      reached.has(service.host_id),
  );
}

/**
 * The services that show down but that the watcher will open no incident for (#478): down, with
 * nothing it waits on, no incident open, not quiet, on a host Bleep reaches. Defined as what is
 * left, so Bleep never says nothing needs you beside a row shown down; the backend's
 * `unwatched` only says why.
 */
export function unwatchedDownServices<S extends MoodService>(
  services: readonly S[],
  hosts: readonly Pick<HostView, "id" | "reachable">[],
): S[] {
  const reached = new Set(hosts.filter((host) => host.reachable).map((host) => host.id));
  return services.filter(
    (service) =>
      service.health === "down" &&
      service.watching.length === 0 &&
      service.quiet === null &&
      service.open_incident_id === null &&
      reached.has(service.host_id),
  );
}

/** A host out of reach whose window the watcher is waiting out before it opens an incident. */
export function isWatchedHost(
  host: Pick<HostView, "reachable" | "may_sleep" | "quiet" | "watching">,
) {
  return isOutOfReach(host) && host.watching !== null;
}

/**
 * The overview's mood from the incidents, the hosts and the services. `unsureIds` holds the
 * waiting incidents whose diagnosis is unsure (read from their detail); without it every waiting
 * plan needs you. A quiet subject's failed run and a quiet host's unreachability do not count.
 * A host out of reach is Working, unless the watcher still waits out its window: then, as for a
 * service the watcher waits on (`watchedServices`), Bleep watches. A service shown down that the
 * watcher opens nothing for (`unwatchedDownServices`) makes Bleep point at it.
 */
export function overallMood(
  incidents: readonly Incident[],
  hosts: readonly QuietHost[],
  unsureIds: ReadonlySet<string> = new Set(),
  now: number = Date.now(),
  services: readonly MoodService[] = [],
): Mood {
  const waiting = incidents.filter((incident) => incident.status === "awaiting_approval");
  const failedForYou = (incident: Incident) =>
    failedRecently(incident, now) && !isQuietSubject(incident.subject, hosts);
  if (incidents.some(failedForYou)) return "needs_you";
  if (waiting.some((incident) => !unsureIds.has(incident.id))) return "needs_you";
  if (waiting.length > 0) return "not_sure";
  if (incidents.some((incident) => WORKING.has(incident.status))) return "working";
  if (hosts.some((host) => isOutOfReach(host) && !isWatchedHost(host))) return "working";
  if (hosts.some(isWatchedHost)) return "watching";
  if (watchedServices(services, hosts).length > 0) return "watching";
  if (unwatchedDownServices(services, hosts).length > 0) return "pointing";
  return "all_good";
}

const AMBER: ReadonlySet<Mood> = new Set(["working", "watching", "pointing"]);

/** The header's three-colour state; it has no Not sure, since a waiting plan is red either way.
 * Watching and Pointing are amber like Working: something is wrong and Bleep's attention is on
 * it, though only the human can start a look at what Bleep points at. */
export function overallState(
  incidents: readonly Incident[],
  hosts: readonly QuietHost[],
  now: number = Date.now(),
  services: readonly MoodService[] = [],
): OverallState {
  const mood = overallMood(incidents, hosts, new Set(), now, services);
  if (mood === "needs_you") return "red";
  if (AMBER.has(mood)) return "amber";
  return "green";
}

/** The face the header's logo and the chat button show: the state's own (`stateMood`), except
 * that Bleep points while the overall mood is pointing (#478). */
export function faceMood(state: OverallState, mood: Mood): Mood {
  return mood === "pointing" ? mood : stateMood(state);
}

/** The mood the head-only Bleep shows for a state, so its eyes take the state's colour. */
export function stateMood(state: OverallState): Mood {
  if (state === "red") return "needs_you";
  if (state === "amber") return "working";
  return "all_good";
}
