"use client";

import { Loader2, ThumbsDown, ThumbsUp, TriangleAlert } from "lucide-react";
import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";
import type { Feedback, FeedbackBody, FeedbackTarget, Vote } from "@/lib/api-types";

const MAX_NOTE = 200; // the API's limit (NOTE_CHARS)
const FINDING: Record<FeedbackTarget, string> = { diagnosis: "diagnosis", proposal: "plan" };
const VOTES: { vote: Vote; label: string; Icon: typeof ThumbsUp }[] = [
  { vote: "up", label: "Right", Icon: ThumbsUp },
  { vote: "down", label: "Wrong", Icon: ThumbsDown },
];

/**
 * Thumbs up or down on the incident's diagnosis or plan, with an optional note. A vote is sent at
 * once; a later one replaces it. Bleep keeps it with the incident and shows it to the agent on the
 * next similar incident.
 */
export function FeedbackBar({
  incidentId,
  target,
  current,
  onSaved,
}: {
  incidentId: string;
  target: FeedbackTarget;
  /** The vote the backend has for this target, if any. */
  current?: Feedback;
  /** Called after a vote was stored, to reload the incident. */
  onSaved?: () => void;
}) {
  const [mine, setMine] = useState<Feedback | null>(null);
  const [sending, setSending] = useState<Vote | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [noting, setNoting] = useState(false);
  const [note, setNote] = useState("");
  const noteId = useId();

  const saved = mine ?? current ?? null;
  const finding = FINDING[target];

  async function send(vote: Vote) {
    const body: FeedbackBody = { target, vote };
    if (note.trim()) body.note = note.trim();
    setSending(vote);
    setFailure(null);
    try {
      const stored = await apiFetch<Feedback>(
        `/api/incidents/${encodeURIComponent(incidentId)}/feedback`,
        { method: "POST", body: JSON.stringify(body) },
      );
      setMine(stored);
      setNoting(false);
      setNote("");
      onSaved?.();
    } catch (error) {
      setFailure(error instanceof Error ? error.message : String(error));
    } finally {
      setSending(null);
    }
  }

  return (
    <div className="flex flex-col gap-2 border-t border-border pt-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">Was this {finding} right?</span>
        {VOTES.map(({ vote, label, Icon }) => (
          <Button
            key={vote}
            size="sm"
            variant={saved?.vote === vote ? "default" : "outline"}
            aria-pressed={saved?.vote === vote}
            disabled={sending !== null}
            onClick={() => void send(vote)}
          >
            {sending === vote ? (
              <Loader2 aria-hidden className="animate-spin" />
            ) : (
              <Icon aria-hidden />
            )}
            {label}
          </Button>
        ))}
        <Button
          size="sm"
          variant="ghost"
          aria-expanded={noting}
          onClick={() => setNoting((open) => !open)}
        >
          Add a note
        </Button>
      </div>

      {noting && (
        <div className="flex flex-col gap-1">
          <label htmlFor={noteId} className="text-xs text-muted-foreground">
            Your note goes with the vote you pick next.
          </label>
          <textarea
            id={noteId}
            value={note}
            maxLength={MAX_NOTE}
            rows={2}
            onChange={(changed) => setNote(changed.target.value)}
            className="rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          />
        </div>
      )}

      {saved && (
        <p role="status" className="text-xs text-muted-foreground">
          {saved.vote === "up" ? "Marked right" : "Marked wrong"}
          {saved.note ? `: “${saved.note}”` : ""}. Bleep remembers this for similar incidents.
        </p>
      )}
      {failure && (
        <p role="alert" className="flex items-center gap-2 text-xs text-health-down">
          <TriangleAlert aria-hidden className="size-3.5" />
          Could not save the vote: {failure}
        </p>
      )}
    </div>
  );
}
