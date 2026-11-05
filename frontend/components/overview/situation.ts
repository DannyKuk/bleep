import type { HostView, Incident, IncidentDetail, ServiceView } from "@/lib/api-types";
import { overallMood, type Mood } from "@/lib/mood";

/** What the overview's hero is about, picked in the order of Bleep's moods. */
export type Situation =
  /** An incident: waiting for the human, failed, or one Bleep works on. `others` counts the other
   * incidents that need the human too. */
  | { kind: "incident"; incident: Incident; detail: IncidentDetail | undefined; others: number }
  /** A host Bleep cannot reach, with the incident about it when there is one. */
  | { kind: "unreachable"; host: HostView; incident: Incident | undefined }
  | { kind: "quiet" }
  | { kind: "no_hosts" };

export interface HeroInput {
  incidents: readonly Incident[];
  hosts: readonly HostView[];
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
 * plan Bleep is unsure of, then an unreachable host, then an incident Bleep works on. Each
 * incident's mood is the overall mood of that incident alone, so the hero always agrees with the
 * face Bleep shows beside it.
 */
export function heroSituation({ incidents, hosts, details, now }: HeroInput): Situation {
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
  const unreachable = hosts.find((host) => !host.reachable && !host.quiet);
  if (unreachable) {
    const about = working.find(
      (incident) => incident.subject.kind === "host" && incident.subject.host_id === unreachable.id,
    );
    return { kind: "unreachable", host: unreachable, incident: about };
  }
  if (working.length > 0) {
    return { kind: "incident", incident: working[0], detail: details[working[0].id], others: 0 };
  }
  return hosts.length === 0 ? { kind: "no_hosts" } : { kind: "quiet" };
}

const COUNT_WORDS = [
  "zero",
  "one",
  "two",
  "three",
  "four",
  "five",
  "six",
  "seven",
  "eight",
  "nine",
];

const countWord = (n: number) => COUNT_WORDS[n] ?? String(n);

/** "A", "A and B", "A, B and C". */
export function joinNames(names: readonly string[]): string {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

const serviceHurting = (service: ServiceView) =>
  service.quiet === null && (service.health === "down" || service.health === "degraded");

/** A host with a problem: out of reach, or a service down or degraded. A quiet host, or one whose
 * only hurting services are quiet, is left alone and does not count. */
export function isHurting(host: HostView, services: readonly ServiceView[]): boolean {
  if (host.quiet) return false;
  if (!host.reachable) return true;
  const own = services.filter((service) => service.host_id === host.id);
  const hurtingOwn = own.filter(
    (service) => service.health === "down" || service.health === "degraded",
  );
  if (hurtingOwn.length > 0 && hurtingOwn.every((service) => service.quiet !== null)) return false;
  return host.health === "down" || host.health === "degraded";
}

function servicesReach(host: HostView, services: readonly ServiceView[]): string {
  const own = services.filter((service) => service.host_id === host.id);
  const hurting = own.filter(serviceHurting).length;
  if (hurting === 0) return "";
  if (own.length === 1) return ": its only service";
  if (hurting === own.length) return `: all ${hurting} of its services`;
  return `: ${hurting} of its ${own.length} services`;
}

function hurtingSentence(
  hurting: readonly HostView[],
  services: readonly ServiceView[],
  hostCount: number,
): string {
  if (hurting.length > 1) return `${joinNames(hurting.map((host) => host.name))} are hurting.`;
  const [host] = hurting;
  if (!host.reachable) return `${host.name} is out of reach as a whole.`;
  const only = hostCount > 1 ? "Only " : "";
  return `${only}${host.name} is hurting${servicesReach(host, services)}.`;
}

function fineSentence(fine: number, hostCount: number): string {
  if (fine === hostCount) {
    if (fine === 1) return "It is fine.";
    if (fine === 2) return "Both are fine.";
    return `All ${countWord(fine)} are fine.`;
  }
  if (fine === 0) return "";
  return fine === 1 ? "The other one is fine." : `The other ${countWord(fine)} are fine.`;
}

/** The line above the host tiles: how far a problem reaches, and that the rest is fine. */
export function reachLine(hosts: readonly HostView[], services: readonly ServiceView[]): string {
  const hurting = hosts.filter((host) => isHurting(host, services));
  const fine = fineSentence(hosts.length - hurting.length, hosts.length);
  if (hurting.length === 0) return fine;
  return [hurtingSentence(hurting, services, hosts.length), fine].filter(Boolean).join(" ");
}
