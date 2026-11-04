import type { Channel } from "@/lib/api-types";

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

const INVISIBLE = /[\p{Cf}\u034f\u115f\u1160\u17b4\u17b5\u3164\uffa0]/gu;

/** Text with every character that draws nothing (zero-width, bidi marks, fillers) shown as "\uFFFD",
 * the way the Telegram message shows a step, so a name cannot hide in a plan's params. */
export function showInvisible(text: string): string {
  return text.replace(INVISIBLE, "\uFFFD");
}
