import { apiFetch } from "@/lib/api";
import type { QuietView } from "@/lib/api-types";

export interface QuietTarget {
  scope: "service" | "host";
  id: string;
  name: string;
}

export type QuietBody = { mode: "snooze"; until: string } | { mode: "leave_alone" };

export type SnoozeChoice = "1h" | "4h" | "morning";

const HOUR_MS = 60 * 60 * 1000;

/** When a snooze chosen at `now` ends; "morning" is the next 08:00 local time after `now`. */
export function snoozeUntil(choice: SnoozeChoice, now: Date): Date {
  if (choice === "1h") return new Date(now.getTime() + HOUR_MS);
  if (choice === "4h") return new Date(now.getTime() + 4 * HOUR_MS);
  const morning = new Date(now);
  morning.setHours(8, 0, 0, 0);
  if (morning <= now) morning.setDate(morning.getDate() + 1);
  return morning;
}

const clock = (at: Date) => `${at.getHours()}:${String(at.getMinutes()).padStart(2, "0")}`;

function untilWords(until: Date, now: Date): string {
  const tomorrow = new Date(now);
  tomorrow.setDate(tomorrow.getDate() + 1);
  if (until.toDateString() === now.toDateString()) return clock(until);
  if (until.toDateString() === tomorrow.toDateString()) return `tomorrow ${clock(until)}`;
  return `${until.toLocaleDateString("en-GB", { weekday: "short" })} ${clock(until)}`;
}

/** When a quiet state began: "14:02" today, "yesterday 14:02", else the weekday and time. */
export function sinceWords(since: Date, now: Date): string {
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  if (since.toDateString() === now.toDateString()) return clock(since);
  if (since.toDateString() === yesterday.toDateString()) return `yesterday ${clock(since)}`;
  return `${since.toLocaleDateString("en-GB", { weekday: "short" })} ${clock(since)}`;
}

/** "Left alone", "Snoozed until 18:00"; "Host …" when a service is quiet through its host. */
export function quietLabel(quiet: QuietView, now: Date): string {
  const words =
    quiet.mode === "leave_alone" || quiet.until === null
      ? "left alone"
      : `snoozed until ${untilWords(new Date(quiet.until), now)}`;
  const label = quiet.scope === "host" ? `Host ${words}` : words;
  return label.charAt(0).toUpperCase() + label.slice(1);
}

/** The service id holds a `/` between host and key, which the route's path parameter keeps. */
function pathOf({ scope, id }: QuietTarget): string {
  const encoded = id.split("/").map(encodeURIComponent).join("/");
  return `/api/quiet/${scope === "service" ? "services" : "hosts"}/${encoded}`;
}

export const setQuiet = (target: QuietTarget, body: QuietBody) =>
  apiFetch<QuietView>(pathOf(target), { method: "PUT", body: JSON.stringify(body) });

export const clearQuiet = (target: QuietTarget) =>
  apiFetch<void>(pathOf(target), { method: "DELETE" });

/** The menu's words for the "morning" snooze: "Until 8:00" in the small hours, else tomorrow's. */
export function morningLabel(now: Date): string {
  const isToday = snoozeUntil("morning", now).toDateString() === now.toDateString();
  return isToday ? "Until 8:00" : "Until tomorrow 8:00";
}
