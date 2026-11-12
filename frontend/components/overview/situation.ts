import type {
  ContainerView,
  HostView,
  Incident,
  IncidentDetail,
  ServiceView,
} from "@/lib/api-types";
import type { Messages } from "@/lib/i18n";
import { isAsleep, isOutOfReach, overallMood, watchedServices, type Mood } from "@/lib/mood";

/** What the overview's hero is about, picked in the order of Bleep's moods. */
export type Situation =
  /** An incident: waiting for the human, failed, or one Bleep works on. `others` counts the other
   * incidents that need the human too. */
  | { kind: "incident"; incident: Incident; detail: IncidentDetail | undefined; others: number }
  /** A host Bleep cannot reach, with the incident about it when there is one. */
  | { kind: "unreachable"; host: HostView; incident: Incident | undefined }
  /** Services down with no incident open for them yet (`watchedServices`). */
  | { kind: "watching"; services: ServiceView[] }
  | { kind: "quiet" }
  | { kind: "no_hosts" };

export interface HeroInput {
  incidents: readonly Incident[];
  hosts: readonly HostView[];
  services?: readonly ServiceView[];
  /** Details of the incidents that wait for the human, by id. */
  details: Readonly<Record<string, IncidentDetail>>;
  now: number;
}

/** The waiting incidents whose diagnosis says Bleep is not sure. */
export function unsureIds(details: Readonly<Record<string, IncidentDetail>>): Set<string> {
  return new Set(
    Object.entries(details)
      .filter(([, detail]) => detail.diagnosis?.unsure)
      .map(([id]) => id),
  );
}

const awaitingFirst = (a: Incident, b: Incident) =>
  Number(b.status === "awaiting_approval") - Number(a.status === "awaiting_approval");

/**
 * The hero's subject: what needs the human (a waiting plan before a failed run), then a waiting
 * plan Bleep is unsure of, then an unreachable host, then an incident Bleep works on, then the
 * services Bleep watches. Each incident's mood is the overall mood of that incident alone, so the
 * hero always agrees with the face Bleep shows beside it.
 */
export function heroSituation({
  incidents,
  hosts,
  services = [],
  details,
  now,
}: HeroInput): Situation {
  const unsure = unsureIds(details);
  // Only the hosts' quiet state matters here: an unreachable host must not turn every incident
  // into "working", the hero picks the unreachable host itself below.
  const reachable = hosts.map((host) => ({ ...host, reachable: true }));
  const moodOf = (incident: Incident): Mood => overallMood([incident], reachable, unsure, now);
  const withMood = (mood: Mood) => incidents.filter((incident) => moodOf(incident) === mood);

  const needsYou = [...withMood("needs_you").sort(awaitingFirst), ...withMood("not_sure")];
  if (needsYou.length > 0) {
    const [first] = needsYou;
    return {
      kind: "incident",
      incident: first,
      detail: details[first.id],
      others: needsYou.length - 1,
    };
  }
  const working = withMood("working");
  const unreachable = hosts.find(isOutOfReach);
  if (unreachable) {
    const about = working.find(
      (incident) => incident.subject.kind === "host" && incident.subject.host_id === unreachable.id,
    );
    return { kind: "unreachable", host: unreachable, incident: about };
  }
  if (working.length > 0) {
    return { kind: "incident", incident: working[0], detail: details[working[0].id], others: 0 };
  }
  const watched = watchedServices(services, hosts);
  if (watched.length > 0) return { kind: "watching", services: watched };
  return hosts.length === 0 ? { kind: "no_hosts" } : { kind: "quiet" };
}

/** What a container shows of its trouble, in Docker's own words: "unhealthy", "exited (1)". */
function containerTrouble(container: ContainerView): string {
  if (container.state === "running") return `${container.name} · ${container.health}`;
  const code = container.exit_code === null ? "" : ` (${container.exit_code})`;
  return `${container.name} · ${container.state}${code}`;
}

export interface WatchWords {
  /** Why Bleep watches, after the service's name: "its URL check failed once". */
  reason: string;
  /** When it looks into it: "If the next check fails too, I will look into it." */
  look: string;
  /** The container or URL in mono: "shop-web-1 · exited (1)", "http://shop/ · 503". */
  detail?: string;
}

/**
 * What the watcher waits on for a service, in words: a death inside its grace window before a
 * URL's failures in a row. The count and the time come from the watcher (`watching`), so the
 * words never claim more than it has seen. `when` writes a time in the person's zone.
 */
export function watchReason(
  service: ServiceView,
  t: Messages,
  when: (iso: string) => string,
): WatchWords {
  const o = t.overview;
  const watch =
    service.watching.find((candidate) => candidate.kind === "container") ?? service.watching[0];
  if (watch?.kind === "container") {
    const container = service.containers.find((candidate) => candidate.name === watch.container);
    return {
      reason: o.containerDied,
      look: watch.opens_after ? o.lookAfterTime(when(watch.opens_after)) : o.lookIfOne,
      detail: container ? containerTrouble(container) : (watch.container ?? undefined),
    };
  }
  const status = service.urls.find((url) => url.url === watch?.url);
  const code = status?.status_code ? ` · ${status.status_code}` : "";
  const left = (watch?.failures_to_open ?? 0) - (watch?.failures ?? 0);
  return {
    reason: watch?.failures ? o.urlFailed(watch.failures) : o.urlFailing,
    look: left > 0 ? o.lookAfterChecks(left) : o.lookIfOne,
    detail: watch?.url ? `${watch.url}${code}` : undefined,
  };
}

const NAMED_AT_MOST = 3;

/** The watched services' names for one sentence: at most three, else two and how many more. */
export function watchedNames(services: readonly ServiceView[], t: Messages): string {
  const names = services.map((service) => service.name);
  if (names.length <= NAMED_AT_MOST) return joinNames(names, t);
  const shown = names.slice(0, NAMED_AT_MOST - 1);
  return joinNames([...shown, t.overview.moreServices(names.length - shown.length)], t);
}

const countWord = (n: number, t: Messages) => t.overview.numbers[n] ?? String(n);

/** "A", "A and B", "A, B and C". */
export function joinNames(names: readonly string[], t: Messages): string {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} ${t.overview.and} ${names[names.length - 1]}`;
}

const serviceHurting = (service: ServiceView) =>
  service.quiet === null && (service.health === "down" || service.health === "degraded");

/** A host with a problem: out of reach, or a service down or degraded. A quiet host, an asleep
 * one, or one whose only hurting services are quiet, is left alone and does not count. */
export function isHurting(host: HostView, services: readonly ServiceView[]): boolean {
  if (host.quiet || isAsleep(host)) return false;
  if (!host.reachable) return true;
  const own = services.filter((service) => service.host_id === host.id);
  const hurtingOwn = own.filter(
    (service) => service.health === "down" || service.health === "degraded",
  );
  if (hurtingOwn.length > 0 && hurtingOwn.every((service) => service.quiet !== null)) return false;
  return host.health === "down" || host.health === "degraded";
}

function servicesReach(host: HostView, services: readonly ServiceView[], t: Messages): string {
  const own = services.filter((service) => service.host_id === host.id);
  const hurting = own.filter(serviceHurting).length;
  if (hurting === 0) return "";
  if (own.length === 1) return t.overview.reachOnly;
  if (hurting === own.length) return t.overview.reachAll(hurting);
  return t.overview.reachSome(hurting, own.length);
}

function hurtingSentence(
  hurting: readonly HostView[],
  services: readonly ServiceView[],
  hostCount: number,
  t: Messages,
): string {
  if (hurting.length > 1) {
    return t.overview.manyHurting(
      joinNames(
        hurting.map((host) => host.name),
        t,
      ),
    );
  }
  const [host] = hurting;
  if (!host.reachable) return t.overview.outOfReach(host.name);
  return t.overview.oneHurting(host.name, hostCount > 1, servicesReach(host, services, t));
}

function fineSentence(fine: number, hostCount: number, t: Messages): string {
  const o = t.overview;
  if (fine === hostCount) {
    if (fine === 1) return o.fineOne;
    if (fine === 2) return o.fineBoth;
    return o.fineAll(countWord(fine, t));
  }
  if (fine === 0) return "";
  return fine === 1 ? o.otherOne : o.otherMany(countWord(fine, t));
}

/** The line above the host tiles: how far a problem reaches, and that the rest is fine. */
export function reachLine(
  hosts: readonly HostView[],
  services: readonly ServiceView[],
  t: Messages,
): string {
  const hurting = hosts.filter((host) => isHurting(host, services));
  const fine = fineSentence(hosts.length - hurting.length, hosts.length, t);
  if (hurting.length === 0) return fine;
  return [hurtingSentence(hurting, services, hosts.length, t), fine].filter(Boolean).join(" ");
}
