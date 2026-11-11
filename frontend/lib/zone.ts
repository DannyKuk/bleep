import { FIXED_LOCALE } from "@/lib/i18n";
import type { Language } from "@/lib/settings-types";

/**
 * The time zone every time is shown in: the IANA name chosen on the settings page, or undefined
 * for the browser's own. The API sends every time in UTC; it is turned into this zone only when
 * it is shown, so a change of zone changes every time on the page and nothing stored (#400).
 */
export type Zone = string | undefined;

/** `name` when this browser can show times in it, else undefined: the browser's own zone. */
export function usableZone(name: string | null | undefined): Zone {
  if (!name) return undefined;
  try {
    new Intl.DateTimeFormat("en-GB", { timeZone: name });
    return name;
  } catch {
    return undefined;
  }
}

/** The browser's own zone, "Europe/Vienna". */
export const browserZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone;

interface Wall {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
  second: number;
}

const formats = new Map<string, Intl.DateTimeFormat>();

/** What a clock on the wall in `zone` shows at `at`. */
function wall(at: Date, zone: Zone): Wall {
  const key = zone ?? "";
  let format = formats.get(key);
  if (!format) {
    format = new Intl.DateTimeFormat("en-US", {
      timeZone: zone,
      hourCycle: "h23",
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "numeric",
      minute: "numeric",
      second: "numeric",
    });
    formats.set(key, format);
  }
  const part = Object.fromEntries(format.formatToParts(at).map((p) => [p.type, p.value]));
  return {
    year: Number(part.year),
    month: Number(part.month),
    day: Number(part.day),
    hour: Number(part.hour),
    minute: Number(part.minute),
    second: Number(part.second),
  };
}

/** The day `at` falls on in `zone`, moved by `days`: "2026-11-05". */
export function dayIn(at: Date, zone: Zone, days = 0): string {
  const { year, month, day } = wall(at, zone);
  return new Date(Date.UTC(year, month - 1, day + days)).toISOString().slice(0, 10);
}

/** The moment the clock in `zone` shows `hour`:00 on `day` ("2026-11-05"). */
export function atHourIn(day: string, hour: number, zone: Zone): Date {
  const [year, month, date] = day.split("-").map(Number);
  const asUtc = Date.UTC(year, month - 1, date, hour);
  const offset = (at: number) => {
    const w = wall(new Date(at), zone);
    return Date.UTC(w.year, w.month - 1, w.day, w.hour, w.minute, w.second) - at;
  };
  // The second look corrects the first when a clock change lies between the guess and the hour.
  return new Date(asUtc - offset(asUtc - offset(asUtc)));
}

/** "9:05" in `zone`: the hour without a leading zero. */
export function clockIn(at: Date, zone: Zone): string {
  const { hour, minute } = wall(at, zone);
  return `${hour}:${String(minute).padStart(2, "0")}`;
}

/** "Tue" (in German "Di") in `zone`. */
export const weekdayIn = (at: Date, zone: Zone, language: Language) =>
  at.toLocaleDateString(FIXED_LOCALE[language], { weekday: "short", timeZone: zone });
