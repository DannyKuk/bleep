"use client";

import { Loader2, ThumbsDown, ThumbsUp, TriangleAlert } from "lucide-react";
import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";
import type { Feedback, FeedbackBody, FeedbackTarget, Vote } from "@/lib/api-types";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const MAX_NOTE = 200; // the API's limit (NOTE_CHARS)
const VOTES: { vote: Vote; Icon: typeof ThumbsUp; pressed: string }[] = [
  { vote: "up", Icon: ThumbsUp, pressed: "border-ok bg-ok-bg text-ok" },
  { vote: "down", Icon: ThumbsDown, pressed: "border-down bg-down-bg text-down" },
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
  const t = useT().feedback;

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
    <div className="flex flex-col gap-2 border-t border-line pt-3 print:hidden">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-bold text-ink-2">{t.question[target]}</span>
        {VOTES.map(({ vote, Icon, pressed }) => (
          <Button
            key={vote}
            size="sm"
            variant="outline"
            className={cn(saved?.vote === vote && pressed)}
            aria-pressed={saved?.vote === vote}
            disabled={sending !== null}
            onClick={() => void send(vote)}
          >
            {sending === vote ? (
              <Loader2 aria-hidden className="animate-spin" />
            ) : (
              <Icon aria-hidden />
            )}
            {vote === "up" ? t.right : t.wrong}
          </Button>
        ))}
        <Button
          size="sm"
          variant="ghost"
          aria-expanded={noting}
          onClick={() => setNoting((open) => !open)}
        >
          {t.addNote}
        </Button>
      </div>

      {noting && (
        <div className="flex flex-col gap-1">
          <label htmlFor={noteId} className="text-xs text-muted-foreground">
            {t.noteHint}
          </label>
          <textarea
            id={noteId}
            value={note}
            maxLength={MAX_NOTE}
            rows={2}
            onChange={(changed) => setNote(changed.target.value)}
            className="rounded-field border border-line bg-surface px-3 py-2 text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          />
        </div>
      )}

      {saved && (
        <p role="status" className="text-xs text-muted-foreground">
          {t.saved(saved.vote === "up", saved.note ?? null)}
        </p>
      )}
      {failure && (
        <p role="alert" className="flex items-center gap-2 text-xs text-down">
          <TriangleAlert aria-hidden className="size-3.5" />
          {t.failed(failure)}
        </p>
      )}
    </div>
  );
}
