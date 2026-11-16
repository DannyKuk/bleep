"use client";

import { useEffect, useState } from "react";

import { Answer } from "@/components/chat/answer";
import { useT } from "@/lib/i18n";
import { fetchLook, type LookProgress } from "@/lib/talk-api";

// How often a look the panel follows is asked about (#458): often while it runs, seldom while it
// waits (for a yes on the incident's page, for the model or for its host).
const LOOK_RUNNING_MS = 2_000;
const LOOK_WAITING_MS = 10_000;

/**
 * A look the panel started, followed until its incident ends (#458): what the run does now, its
 * summary once its plan waits or it ended, and what it did. Asked about every 2 s while it runs
 * and every 10 s while it waits; a failed ask keeps the last answer, says so and asks again. Only
 * an answer that shows something new scrolls the chat down. It stops at the end, or when the
 * panel closes.
 */
export function LookFollow({ incidentId, onChange }: { incidentId: string; onChange: () => void }) {
  const t = useT().chat;
  const [look, setLook] = useState<LookProgress | null>(null);
  const [lost, setLost] = useState(false);

  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;
    let pace = LOOK_RUNNING_MS;
    async function ask() {
      try {
        const now = await fetchLook(incidentId);
        if (stopped) return;
        setLook((shown) => (shows(shown, now) ? shown : now));
        setLost(false);
        if (now.state === "ended") return;
        pace = now.state === "waiting" ? LOOK_WAITING_MS : LOOK_RUNNING_MS;
      } catch {
        if (stopped) return;
        setLost(true);
      }
      timer = window.setTimeout(ask, pace);
    }
    void ask();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [incidentId]);

  useEffect(onChange, [look, lost, onChange]);

  if (!look && !lost) return null;
  return (
    <div
      data-testid="look"
      className="rounded-[18px] rounded-bl-md border border-line bg-surface px-3.5 py-2.5 break-words whitespace-pre-line"
    >
      {look && (
        <p className={look.state === "running" ? "text-ink-2" : undefined}>
          <Answer text={look.line} links={look.links} />
        </p>
      )}
      {look && look.steps.length > 0 && (
        <>
          <p className="mt-2 text-[13px] font-bold text-ink-2">{t.whatIDid}</p>
          <ul aria-label={t.whatIDid} className="list-disc pl-5 text-[13.5px] whitespace-normal">
            {look.steps.map((step, index) => (
              <li key={index}>{step}</li>
            ))}
          </ul>
        </>
      )}
      {lost && (
        <p role="alert" className="mt-1 text-[13px] font-bold text-down">
          {t.lost}
        </p>
      )}
    </div>
  );
}

/** Whether `next` shows what `shown` does, so a poll that brought nothing new changes nothing. */
function shows(shown: LookProgress | null, next: LookProgress): boolean {
  return shown !== null && JSON.stringify(shown) === JSON.stringify(next);
}
