"use client";

import { ArrowUp, Download, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { Bleep } from "@/components/bleep/bleep";
import { useOverall } from "@/components/overall-state";
import { usePinnedBarSpace } from "@/components/pinned-bar";
import { ApiError } from "@/lib/api";
import { stateMood } from "@/lib/mood";
import { idFromSegment } from "@/lib/routes";
import {
  MESSAGE_MAX_CHARS,
  TALK_LOG_MARKDOWN,
  isFinished,
  linkHref,
  pressConfirm,
  sendMessage,
  splitLinks,
  type ChatConfirm,
  type TalkLink,
} from "@/lib/talk-api";

const INCIDENT_PAGE = /^\/incidents\/([^/]+)$/;
const TICK_MS = 15_000; // how often an open offer is checked for its expiry
const BAR_GAP_PX = 12;

type Offer = ChatConfirm & { line?: string; pressing?: boolean; failed?: string };

type Turn =
  | { key: number; who: "you"; text: string }
  | { key: number; who: "bleep"; answer: string; links: TalkLink[]; offer?: Offer }
  | { key: number; who: "error"; text: string };

type NewTurn = Turn extends infer T ? (T extends Turn ? Omit<T, "key"> : never) : never;

function failure(error: unknown): string {
  if (error instanceof ApiError && error.status === 422) return "That message is too long.";
  if (error instanceof ApiError && error.detail) return `Bleep could not answer: ${error.detail}.`;
  return "Bleep could not be reached. Try again.";
}

/**
 * Talking to Bleep from any page (#391): Bleep's face in the bottom-right corner opens a panel
 * with the conversation. It sits in the app layout, so the conversation stays across page
 * changes. Every word Bleep says comes from the backend, which shares the reading and the
 * actions with Telegram but keeps this conversation apart; an action comes back as two buttons
 * and only a press runs it.
 */
export function ChatPanel() {
  const { state, incidents } = useOverall();
  const pathname = usePathname();
  const barSpace = usePinnedBarSpace();
  const [open, setOpen] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const nextKey = useRef(0);
  const list = useRef<HTMLOListElement>(null);
  const input = useRef<HTMLInputElement>(null);

  const pageIncident = pathname.match(INCIDENT_PAGE)?.[1];
  const incidentId = pageIncident ? idFromSegment(pageIncident) : null;
  const about = incidentId && dismissed !== incidentId ? incidentId : null;
  const aboutName = incidents?.find((i) => i.id === about)?.short_name ?? "this incident";
  const mood = state ? stateMood(state) : "not_sure";

  const expired = (offer: Offer) => {
    if (Date.parse(offer.expires_at) <= now) return true;
    const incident = offer.incident_id && incidents?.find((i) => i.id === offer.incident_id);
    return Boolean(incident && isFinished(incident.status));
  };
  const waiting = turns.some(
    (t) => t.who === "bleep" && t.offer && !t.offer.line && !expired(t.offer),
  );

  useEffect(() => {
    if (!waiting) return;
    const timer = window.setInterval(() => setNow(Date.now()), TICK_MS);
    return () => window.clearInterval(timer);
  }, [waiting]);

  useEffect(() => {
    if (open && list.current) list.current.scrollTop = list.current.scrollHeight;
  }, [open, turns, sending]);

  useEffect(() => {
    if (open) input.current?.focus();
  }, [open]);

  const add = (turn: NewTurn) =>
    setTurns((all) => [...all, { ...turn, key: nextKey.current++ } as Turn]);

  async function send(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || sending) return;
    add({ who: "you", text });
    setDraft("");
    setSending(true);
    try {
      const reply = await sendMessage(text, about);
      add({
        who: "bleep",
        answer: reply.answer,
        links: reply.links,
        offer: reply.confirm ?? undefined,
      });
    } catch (error) {
      add({ who: "error", text: failure(error) });
      setDraft(text);
    } finally {
      setSending(false);
    }
  }

  const updateOffer = (key: number, change: Partial<Offer>) =>
    setTurns((all) =>
      all.map((t) =>
        t.key === key && t.who === "bleep" && t.offer
          ? { ...t, offer: { ...t.offer, ...change } }
          : t,
      ),
    );

  async function press(key: number, offer: Offer, yes: boolean) {
    updateOffer(key, { pressing: true, failed: undefined });
    try {
      const { line } = await pressConfirm(offer.id, yes);
      updateOffer(key, { pressing: false, line });
    } catch (error) {
      updateOffer(key, { pressing: false, failed: failure(error) });
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        aria-label={waiting ? "Talk to Bleep (Bleep asked you something)" : "Talk to Bleep"}
        onClick={() => setOpen(true)}
        style={barSpace ? { bottom: barSpace + BAR_GAP_PX } : undefined}
        className="fixed right-5 bottom-5 z-40 grid size-14 place-items-center rounded-full border border-line bg-surface shadow-[0_8px_28px_-8px_rgba(40,30,120,0.35)] hover:bg-soft focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none print:hidden"
      >
        <Bleep mood={mood} size={44} />
        {waiting && (
          <span
            data-testid="chat-dot"
            className="absolute top-0.5 right-0.5 size-3 rounded-full border-2 border-surface bg-accent"
          />
        )}
      </button>
    );
  }

  return (
    <section
      aria-label="Chat with Bleep"
      onKeyDown={(event) => event.key === "Escape" && setOpen(false)}
      className="fixed inset-0 z-50 flex flex-col overflow-hidden bg-surface sm:inset-auto sm:right-5 sm:bottom-5 sm:h-[520px] sm:max-h-[calc(100dvh-40px)] sm:w-[360px] sm:rounded-card-sm sm:border sm:border-line sm:shadow-[0_18px_50px_-12px_rgba(40,30,120,0.40)] print:hidden"
    >
      <header className="flex items-center gap-2.5 border-b border-line px-4 py-3">
        <Bleep mood={mood} size={40} />
        <div className="min-w-0 flex-1">
          <h2 className="text-lg leading-tight font-extrabold">Bleep</h2>
          <p className="text-[13px] text-ink-2">Asks before it changes anything</p>
        </div>
        <a
          href={TALK_LOG_MARKDOWN}
          download
          aria-label="Export chat"
          title="Export chat"
          className="grid size-9 place-items-center rounded-full hover:bg-soft focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          <Download className="size-5" />
        </a>
        <button
          type="button"
          aria-label="Close chat"
          onClick={() => setOpen(false)}
          className="grid size-9 place-items-center rounded-full hover:bg-soft focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          <X className="size-5" />
        </button>
      </header>
      <ol
        ref={list}
        aria-live="polite"
        className="flex flex-1 flex-col gap-3 overflow-y-auto bg-bg px-3 py-4 text-[14.5px] leading-snug"
      >
        {turns.length === 0 && (
          <li className="mx-auto max-w-[85%] py-6 text-center text-[13.5px] text-ink-2">
            Ask how things look, or tell Bleep to leave a service alone for a while.
          </li>
        )}
        {turns.map((turn) => {
          if (turn.who === "you") {
            return (
              <li
                key={turn.key}
                className="ml-auto max-w-[80%] rounded-[18px] rounded-br-md bg-accent px-3.5 py-2 break-words whitespace-pre-line text-accent-ink"
              >
                {turn.text}
              </li>
            );
          }
          if (turn.who === "error") {
            return (
              <li
                key={turn.key}
                role="alert"
                className="mr-auto max-w-[88%] pl-1 text-[13px] font-bold text-down"
              >
                {turn.text}
              </li>
            );
          }
          return (
            <li key={turn.key} className="mr-auto flex max-w-[88%] flex-col gap-2">
              <div className="rounded-[18px] rounded-bl-md border border-line bg-surface px-3.5 py-2.5 break-words whitespace-pre-line">
                <Answer text={turn.answer} links={turn.links} />
              </div>
              {turn.offer && (
                <OfferButtons
                  offer={turn.offer}
                  expired={expired(turn.offer)}
                  onPress={(yes) => press(turn.key, turn.offer!, yes)}
                />
              )}
            </li>
          );
        })}
        {sending && (
          <li className="mr-auto rounded-[18px] rounded-bl-md border border-line bg-surface px-3.5 py-2 text-ink-2">
            Bleep is reading…
          </li>
        )}
      </ol>
      <form
        onSubmit={send}
        className="border-t border-line p-3 pb-[max(env(safe-area-inset-bottom),12px)]"
      >
        {about && (
          <span className="mb-2 inline-flex items-center gap-1.5 rounded-full bg-soft py-1 pr-1 pl-3 text-[13px] font-bold">
            About <span className="font-mono">{aboutName}</span>
            <button
              type="button"
              aria-label={`Leave out ${aboutName}`}
              onClick={() => setDismissed(about)}
              className="grid size-6 place-items-center rounded-full hover:bg-surface focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
            >
              <X className="size-3.5 text-ink-2" />
            </button>
          </span>
        )}
        <div className="flex items-center gap-2 rounded-field border border-line bg-surface py-1.5 pr-1.5 pl-3.5 focus-within:ring-2 focus-within:ring-ring">
          <input
            ref={input}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            maxLength={MESSAGE_MAX_CHARS}
            aria-label="Message to Bleep"
            placeholder={about ? "Ask about this incident…" : "Ask Bleep or tell it what to do…"}
            className="min-w-0 flex-1 bg-transparent text-[14.5px] outline-none placeholder:text-ink-2"
          />
          <button
            type="submit"
            aria-label="Send"
            disabled={sending || !draft.trim()}
            className="grid size-9 shrink-0 place-items-center rounded-full bg-accent text-accent-ink disabled:opacity-50"
          >
            <ArrowUp className="size-4" />
          </button>
        </div>
      </form>
    </section>
  );
}

function Answer({ text, links }: { text: string; links: TalkLink[] }) {
  return (
    <>
      {splitLinks(text, links).map((piece, index) =>
        typeof piece === "string" ? (
          piece
        ) : (
          <Link
            key={index}
            href={linkHref(piece.link)}
            className="font-bold text-accent underline-offset-2 hover:underline"
          >
            {piece.text}
          </Link>
        ),
      )}
    </>
  );
}

function OfferButtons({
  offer,
  expired,
  onPress,
}: {
  offer: Offer;
  expired: boolean;
  onPress: (yes: boolean) => void;
}) {
  if (offer.line || expired) {
    return <p className="pl-1 text-[13px] font-bold text-ink-2">{offer.line ?? "Expired"}</p>;
  }
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex gap-2">
        <button
          type="button"
          disabled={offer.pressing}
          onClick={() => onPress(true)}
          className="h-9 rounded-full bg-accent px-4 text-[14px] font-bold text-accent-ink disabled:opacity-50"
        >
          {offer.yes_label}
        </button>
        <button
          type="button"
          disabled={offer.pressing}
          onClick={() => onPress(false)}
          className="h-9 rounded-full border border-line bg-surface px-4 text-[14px] font-bold disabled:opacity-50"
        >
          {offer.no_label}
        </button>
      </div>
      {offer.failed && (
        <p role="alert" className="pl-1 text-[13px] font-bold text-down">
          {offer.failed}
        </p>
      )}
    </div>
  );
}
