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
