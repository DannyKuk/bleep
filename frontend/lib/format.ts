import type { Channel } from "@/lib/api-types";
import ranges from "@/lib/invisible-ranges.json";
import { clockIn, dayIn, weekdayIn, type Zone } from "@/lib/zone";

/** "18:01:05" in the viewer's locale, in `zone` (the browser's when none is chosen). */
export function formatTime(iso: string, zone?: Zone): string {
  return new Date(iso).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZone: zone,
  });
}

/** "2 Nov 2026, 18:01" in the viewer's locale, in `zone`. */
export function formatDateTime(iso: string, zone?: Zone): string {
  return new Date(iso).toLocaleString([], {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: zone,
  });
}

const DAY_MS = 24 * 60 * 60 * 1000;

/**
 * When something began, short and in `zone`: "14:02" today, "yesterday 14:02", "Tue 14:02"
 * within the last week, else "2 Nov 14:02" (with the year when it is not this one).
 */
export function formatSince(iso: string, now: Date = new Date(), zone?: Zone): string {
  const at = new Date(iso);
  const day = dayIn(at, zone);
  const clock = clockIn(at, zone);
  if (day === dayIn(now, zone)) return clock;
  if (day === dayIn(now, zone, -1)) return `yesterday ${clock}`;
  if (now.getTime() - at.getTime() < 6 * DAY_MS) return `${weekdayIn(at, zone)} ${clock}`;
  const date = at.toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: day.slice(0, 4) === dayIn(now, zone).slice(0, 4) ? undefined : "numeric",
    timeZone: zone,
  });
  return `${date} ${clock}`;
}

/** How a channel is named in a sentence: "on Telegram", "on the dashboard". */
export const CHANNEL: Record<Channel, string> = {
  telegram: "Telegram",
  dashboard: "the dashboard",
};

// The characters the guard treats as invisible (`INVISIBLE_CHARS` in backend/src/bleep/guard/
// sanitize.py), as code point ranges; a backend test keeps the two the same.
const hex = (code: number) => `\\u{${code.toString(16)}}`;
const INVISIBLE = new RegExp(
  `[${(ranges as [number, number][]).map(([a, b]) => hex(a) + (b > a ? "-" + hex(b) : "")).join("")}]`,
  "gu",
);
// Only switch a character between text and emoji look; common after an emoji and no place to hide a name.
const PRESENTATION = /[\ufe0e\ufe0f]/g;

/** Text with every character that draws nothing (zero-width, bidi marks, fillers, variation
 * selectors) shown as "\uFFFD", the way the Telegram message shows a step, so a name cannot hide in
 * a plan's params. U+FE0E and U+FE0F are dropped instead of marked. */
export function showInvisible(text: string): string {
  return text.replace(PRESENTATION, "").replace(INVISIBLE, "\uFFFD");
}
