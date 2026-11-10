// The chat panel's contract: `POST /api/talk/messages` and `POST /api/talk/confirms/{id}`
// (schemas/talk.py). Written by hand like `api-types.ts`. The panel sends the person's words and
// renders what comes back; every word Bleep says is the backend's.

import { ApiError, apiFetch } from "@/lib/api";
import type { IncidentStatus } from "@/lib/api-types";
import { serviceHref } from "@/lib/routes";

/** A page an answer names, found by the backend from Bleep's own state. */
export interface TalkLink {
  kind: "service" | "incident";
  id: string;
  label: string;
}

/** The two buttons under an offer. They expire at `expires_at` or when `incident_id` ends. */
export interface ChatConfirm {
  id: string;
  yes_label: string;
  no_label: string;
  expires_at: string;
  incident_id: string | null;
}

export interface ChatAnswer {
  answer: string;
  links: TalkLink[];
  confirm: ChatConfirm | null;
}

export interface ConfirmResult {
  line: string;
}

/**
 * The talk log as a Markdown transcript (#411): both chats' last messages with how Bleep read
 * each, with the file name the backend gave it. Fetched first, so a lost session is an error
 * the panel can show rather than a saved 401.
 */
export const TALK_LOG_MARKDOWN = "/api/talk/log?format=md";

export async function fetchTalkLog(): Promise<{ blob: Blob; name: string }> {
  const response = await fetch(TALK_LOG_MARKDOWN, { credentials: "same-origin" });
  if (!response.ok) {
    throw new ApiError(response.status, response.statusText || `HTTP ${response.status}`);
  }
  const disposition = response.headers.get("content-disposition") ?? "";
  const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? "bleep-chat.md";
  return { blob: await response.blob(), name };
}

/** As `schemas/talk.py` `MESSAGE_MAX_CHARS`. */
export const MESSAGE_MAX_CHARS = 1000;

export const sendMessage = (text: string, incidentId: string | null) =>
  apiFetch<ChatAnswer>("/api/talk/messages", {
    method: "POST",
    body: JSON.stringify({ text, incident_id: incidentId }),
  });

export const pressConfirm = (confirmId: string, yes: boolean) =>
  apiFetch<ConfirmResult>(`/api/talk/confirms/${encodeURIComponent(confirmId)}`, {
    method: "POST",
    body: JSON.stringify({ yes }),
  });

export function linkHref(link: TalkLink): string {
  return link.kind === "service"
    ? serviceHref(link.id)
    : `/incidents/${encodeURIComponent(link.id)}`;
}

const FINISHED: ReadonlySet<IncidentStatus> = new Set(["resolved", "closed", "failed"]);

export function isFinished(status: IncidentStatus): boolean {
  return FINISHED.has(status);
}

export type Piece = string | { text: string; link: TalkLink };

function escaped(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * The answer cut into text and links: each label where it stands alone (not inside a longer word
 * or name), the longest first, as the backend found them (`talk/links.py`).
 */
export function splitLinks(text: string, links: readonly TalkLink[]): Piece[] {
  const byLabel = new Map<string, TalkLink>();
  for (const link of links)
    if (link.label.trim() && !byLabel.has(link.label)) byLabel.set(link.label, link);
  if (byLabel.size === 0) return [text];
  const labels = [...byLabel.keys()].sort((a, b) => b.length - a.length).map(escaped);
  const pattern = new RegExp(`(?<![\\w-])(?:${labels.join("|")})(?![\\w-])`, "g");
  const pieces: Piece[] = [];
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    const start = match.index ?? 0;
    if (start > last) pieces.push(text.slice(last, start));
    pieces.push({ text: match[0], link: byLabel.get(match[0])! });
    last = start + match[0].length;
  }
  if (last < text.length) pieces.push(text.slice(last));
  return pieces;
}
