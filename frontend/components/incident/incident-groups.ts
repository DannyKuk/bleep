import type { HostView, Incident, IncidentStatus } from "@/lib/api-types";
import { failedRecently, isQuietSubject } from "@/lib/mood";

type QuietHost = Pick<HostView, "id" | "quiet" | "quiet_service_ids">;

export type Group = "needs_you" | "on_it" | "earlier";
export type Filter = "all" | "needs_you" | "open" | "resolved" | "closed_or_failed";

export const GROUP_TITLE: Record<Group, string> = {
  needs_you: "Needs you",
  on_it: "I am on it",
  earlier: "Earlier",
};

export const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "needs_you", label: "Needs you" },
  { id: "open", label: "Open" },
  { id: "resolved", label: "Resolved" },
  { id: "closed_or_failed", label: "Closed or failed" },
];

// A Record over every status, so a new status fails the type check until it is placed. A failed
// run is placed by `groupOf`: it needs the human for a day, as Bleep's mood says, then it is over.
const GROUP_OF: Record<IncidentStatus, Group> = {
  awaiting_approval: "needs_you",
  open: "on_it",
  investigating: "on_it",
  paused: "on_it",
  waiting: "on_it",
  executing: "on_it",
  verifying: "on_it",
  resolved: "earlier",
  closed: "earlier",
  failed: "earlier",
};

/** The incident's group at `now` (ms since the epoch). A failed run of a quiet service or host
 * does not need the human: it was left alone on purpose. */
export function groupOf(incident: Incident, now: number, hosts: readonly QuietHost[] = []): Group {
  const needsYou = failedRecently(incident, now) && !isQuietSubject(incident.subject, hosts);
  return needsYou ? "needs_you" : GROUP_OF[incident.status];
}

export function matchesFilter(
  incident: Incident,
  filter: Filter,
  now: number,
  hosts: readonly QuietHost[] = [],
): boolean {
  const { status } = incident;
  switch (filter) {
    case "all":
      return true;
    case "needs_you":
      return groupOf(incident, now, hosts) === "needs_you";
    case "open":
      return GROUP_OF[status] === "on_it";
    case "resolved":
      return status === "resolved";
    case "closed_or_failed":
      return status === "closed" || status === "failed";
  }
}

export function matchesSearch(incident: Incident, subject: string, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return incident.title.toLowerCase().includes(needle) || subject.toLowerCase().includes(needle);
}

/** Non-empty groups in display order; each keeps the order of the incidents given. */
export function groupIncidents(
  incidents: Incident[],
  now: number,
  hosts: readonly QuietHost[] = [],
): { group: Group; incidents: Incident[] }[] {
  const order: Group[] = ["needs_you", "on_it", "earlier"];
  return order
    .map((group) => ({
      group,
      incidents: incidents.filter((i) => groupOf(i, now, hosts) === group),
    }))
    .filter((g) => g.incidents.length > 0);
}

/** "4 min", "2 h 5 min", "3 d"; "" when the incident is still running or the times are bad. */
export function formatDuration(openedAt: string, closedAt: string | null): string {
  if (!closedAt) return "";
  const ms = new Date(closedAt).getTime() - new Date(openedAt).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "";
  const minutes = Math.round(ms / 60_000);
  if (minutes < 1) return "under 1 min";
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return minutes % 60 ? `${hours} h ${minutes % 60} min` : `${hours} h`;
  return `${Math.floor(hours / 24)} d`;
}

export function headline(
  incidents: Incident[],
  now: number,
  hosts: readonly QuietHost[] = [],
): string {
  if (incidents.length === 0) return "No incidents yet.";
  const needs = incidents.filter((i) => groupOf(i, now, hosts) === "needs_you").length;
  const onIt = incidents.filter((i) => groupOf(i, now, hosts) === "on_it").length;
  if (needs === 0 && onIt === 0) return "Nothing needs you right now.";
  const parts = [];
  if (needs > 0) parts.push(`${needs} ${needs === 1 ? "needs" : "need"} you`);
  if (onIt > 0) parts.push(`I am on ${onIt}`);
  return `${parts.join(", ")}.`;
}
