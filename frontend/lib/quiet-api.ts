import { apiFetch } from "@/lib/api";
import type { QuietView } from "@/lib/api-types";
import { MESSAGES } from "@/lib/i18n";
import type { Language } from "@/lib/settings-types";
import { atHourIn, clockIn, dayIn, weekdayIn, type Zone } from "@/lib/zone";

export interface QuietTarget {
  scope: "service" | "host";
  id: string;
  name: string;
}

export type QuietBody = { mode: "snooze"; until: string } | { mode: "leave_alone" };

export type SnoozeChoice = "1h" | "4h" | "morning";

const HOUR_MS = 60 * 60 * 1000;
const MORNING_HOUR = 8;

/** When a snooze chosen at `now` ends; "morning" is the next 08:00 in `zone` after `now`. */
export function snoozeUntil(choice: SnoozeChoice, now: Date, zone?: Zone): Date {
  if (choice === "1h") return new Date(now.getTime() + HOUR_MS);
  if (choice === "4h") return new Date(now.getTime() + 4 * HOUR_MS);
  const morning = atHourIn(dayIn(now, zone), MORNING_HOUR, zone);
  return morning > now ? morning : atHourIn(dayIn(now, zone, 1), MORNING_HOUR, zone);
}

/** When a snooze ends: "18:00" today, "tomorrow 8:00", else the weekday and time. */
export function untilWords(until: Date, now: Date, zone: Zone, language: Language): string {
  const day = dayIn(until, zone);
  if (day === dayIn(now, zone)) return clockIn(until, zone);
  const tomorrow = MESSAGES[language].dates.tomorrow;
  if (day === dayIn(now, zone, 1)) return `${tomorrow} ${clockIn(until, zone)}`;
  return `${weekdayIn(until, zone, language)} ${clockIn(until, zone)}`;
}

/** When a quiet state began: "14:02" today, "yesterday 14:02", else the weekday and time. */
export function sinceWords(since: Date, now: Date, zone: Zone, language: Language): string {
  const day = dayIn(since, zone);
  if (day === dayIn(now, zone)) return clockIn(since, zone);
  const yesterday = MESSAGES[language].dates.yesterday;
  if (day === dayIn(now, zone, -1)) return `${yesterday} ${clockIn(since, zone)}`;
  return `${weekdayIn(since, zone, language)} ${clockIn(since, zone)}`;
}

/** "Left alone", "Snoozed until 18:00"; "Host …" when a service is quiet through its host. */
export function quietLabel(quiet: QuietView, now: Date, zone: Zone, language: Language): string {
  const t = MESSAGES[language].quiet;
  const host = quiet.scope === "host";
  return quiet.mode === "leave_alone" || quiet.until === null
    ? t.leftAlone(host)
    : t.snoozedUntil(untilWords(new Date(quiet.until), now, zone, language), host);
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
export function morningLabel(now: Date, zone: Zone, language: Language): string {
  const isToday = dayIn(snoozeUntil("morning", now, zone), zone) === dayIn(now, zone);
  const t = MESSAGES[language].quiet;
  return isToday ? t.untilMorning : t.untilTomorrowMorning;
}
