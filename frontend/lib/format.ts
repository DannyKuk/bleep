import type { Channel } from "@/lib/api-types";
import ranges from "@/lib/invisible-ranges.json";

/** "18:01:05" in the viewer's locale and time zone. */
export function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/** "2 Nov 2026, 18:01" in the viewer's locale and time zone. */
export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
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
