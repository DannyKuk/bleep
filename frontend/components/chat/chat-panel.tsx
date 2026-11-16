"use client";

import { ArrowUp, Download, X } from "lucide-react";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import { Bleep } from "@/components/bleep/bleep";
import { Answer } from "@/components/chat/answer";
import { useRegisterChatOpener } from "@/components/chat/chat-opener";
import { LookFollow } from "@/components/chat/look-follow";
import { type Offer, OfferButtons } from "@/components/chat/offer-buttons";
import { useOverall } from "@/components/overall-state";
import { usePinnedBarSpace } from "@/components/pinned-bar";
import { useMediaQuery } from "@/hooks/use-phone";
import { ApiError } from "@/lib/api";
import { type Messages, useT } from "@/lib/i18n";
import { faceMood } from "@/lib/mood";
import { idFromSegment } from "@/lib/routes";
import {
  MESSAGE_MAX_CHARS,
  fetchTalkLog,
  isFinished,
  pressConfirm,
  sendMessage,
  type TalkLink,
} from "@/lib/talk-api";

const INCIDENT_PAGE = /^\/incidents\/([^/]+)$/;
const TICK_MS = 15_000; // how often an open offer is checked for its expiry
const BAR_GAP_PX = 12;
const REVOKE_AFTER_MS = 40_000; // as FileSaver.js waits before freeing a saved file's URL
// As `schemas/talk.py` `ChatMessage.incident_id`: a page path that is no incident id gets no chip.
const INCIDENT_ID = /^[A-Za-z0-9_-]{1,64}$/;
// Below Tailwind's `sm` the panel covers the screen and is a modal dialog; from `sm` up it floats
// in the corner and the page stays usable beside it.
export const SHEET_QUERY = "(max-width: 639.98px)";
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])';
type Turn =
  | { key: number; who: "you"; text: string }
  | { key: number; who: "bleep"; answer: string; links: TalkLink[]; offer?: Offer }
  | { key: number; who: "error"; text: string };

type NewTurn = Turn extends infer T ? (T extends Turn ? Omit<T, "key"> : never) : never;

/** Whether a 422 is about the message's words (FastAPI names the field in `loc`). */
function aboutText(error: ApiError): boolean {
  const detail = (error.body as { detail?: unknown } | null)?.detail;
  return (
    Array.isArray(detail) &&
    detail.some((item) => Array.isArray(item?.loc) && item.loc.includes("text"))
  );
}

function failure(error: unknown, t: Messages["chat"]): string {
  if (error instanceof ApiError && error.status === 422) {
    return aboutText(error) ? t.tooLong : t.notTaken;
  }
  if (error instanceof ApiError && error.detail) return t.couldNotAnswer(error.detail);
  return t.unreachable;
}

function exportFailure(error: unknown, t: Messages["chat"]): string {
  if (error instanceof ApiError && error.status === 401) return t.sessionEnded;
  return t.exportFailed;
}

/**
 * Talking to Bleep from any page (#391): Bleep's face in the bottom-right corner opens a panel
 * with the conversation. It sits in the app layout, so the conversation stays across page
 * changes. Every word Bleep says comes from the backend, which shares the reading and the
 * actions with Telegram but keeps this conversation apart; an action comes back as two buttons
 * and only a press runs it. A page can open it with words already typed (`useAskBleep`).
 */
export function ChatPanel() {
  const t = useT().chat;
  const { state, mood: overall, incidents } = useOverall();
  const pathname = usePathname();
  const barSpace = usePinnedBarSpace();
  const [open, setOpen] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const nextKey = useRef(0);
  const list = useRef<HTMLOListElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const sheet = useMediaQuery(SHEET_QUERY);
  const panel = useRef<HTMLElement>(null);
  const launcher = useRef<HTMLButtonElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const wasOpen = useRef(false);
  const focused = useRef<HTMLElement | null>(null);

  const pageIncident = pathname.match(INCIDENT_PAGE)?.[1];
  const segment = pageIncident ? idFromSegment(pageIncident) : null;
  const incidentId = segment && INCIDENT_ID.test(segment) ? segment : null;
  const about = incidentId && dismissed !== incidentId ? incidentId : null;
  const aboutName = incidents?.find((i) => i.id === about)?.short_name ?? null;
  const mood = state && overall ? faceMood(state, overall) : "not_sure";

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

  const scrollDown = useCallback(() => {
    if (list.current) list.current.scrollTop = list.current.scrollHeight;
  }, []);

  useEffect(() => {
    if (open) scrollDown();
  }, [open, turns, sending, scrollDown]);

  useEffect(() => {
    if (open) input.current?.focus();
  }, [open]);

  const show = useCallback(() => {
    const active = document.activeElement;
    opener.current =
      active instanceof HTMLElement && active !== document.body && active !== launcher.current
        ? active
        : null;
    setOpen(true);
  }, []);

  // Closing puts focus back where the person opened the panel from; the launcher the panel
  // replaced is a new element by then, so it is the fallback.
  useEffect(() => {
    if (!open && wasOpen.current) {
      const back = opener.current?.isConnected ? opener.current : launcher.current;
      back?.focus();
    }
    wasOpen.current = open;
  }, [open]);

  const openWith = useCallback(
    (text: string) => {
      setDraft(text);
      show();
      input.current?.focus();
    },
    [show],
  );
  useRegisterChatOpener(openWith);

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
      add({ who: "error", text: failure(error, t) });
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
      const { line, incident_id } = await pressConfirm(offer.id, yes);
      updateOffer(key, { pressing: false, line, looking: incident_id ?? undefined });
    } catch (error) {
      updateOffer(key, { pressing: false, failed: failure(error, t) });
    }
  }

  async function exportChat() {
    if (exporting) return;
    setExporting(true);
    try {
      const { blob, name } = await fetchTalkLog();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = name;
      link.click();
      // Some browsers read the file after `click` returns; revoking at once can cut the save.
      window.setTimeout(() => URL.revokeObjectURL(url), REVOKE_AFTER_MS);
    } catch (error) {
      add({ who: "error", text: exportFailure(error, t) });
    } finally {
      setExporting(false);
    }
  }

  // A modal sheet keeps focus to itself: Tab wraps at its ends, and focus that lands outside it or
  // on a control that just went away (Send disabled by the cleared draft, an offer's buttons
  // replaced by its line) comes back to the input, so the next Tab never reaches the page behind.
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      const section = panel.current;
      if (!section) return;
      const active = document.activeElement;
      const within = active instanceof HTMLElement && section.contains(active);
      if (event.key === "Escape") {
        if (sheet || within) setOpen(false);
        return;
      }
      if (event.key !== "Tab" || !sheet) return;
      const inside = [...section.querySelectorAll<HTMLElement>(FOCUSABLE)];
      const first = inside[0];
      const last = inside[inside.length - 1];
      if (!first || !last) return;
      if (!within) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };
    const onFocusIn = (event: FocusEvent) => {
      if (sheet && panel.current && !panel.current.contains(event.target as Node)) {
        input.current?.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("focusin", onFocusIn);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("focusin", onFocusIn);
    };
  }, [open, sheet]);

  // Only a control that had focus and then went away is rescued, once: a tap on the transcript,
  // which moves focus to the page's body, is the person's own and is left alone.
  useEffect(() => {
    const had = focused.current;
    if (!open || !sheet || !had) return;
    if (!had.isConnected || had.matches(":disabled")) {
      focused.current = null;
      input.current?.focus();
    }
  });

  if (!open) {
    return (
      <button
        ref={launcher}
        type="button"
        aria-label={waiting ? t.openAsked : t.open}
        onClick={show}
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
      ref={panel}
      tabIndex={-1}
      onFocus={(event) => {
        if (event.target !== event.currentTarget) focused.current = event.target;
      }}
      onBlur={(event) => {
        const target = event.target as HTMLElement;
        if (target === focused.current && target.isConnected && !target.matches(":disabled")) {
          focused.current = null;
        }
      }}
      role={sheet ? "dialog" : undefined}
      aria-modal={sheet ? true : undefined}
      aria-label={t.label}
      className="fixed inset-0 z-50 flex flex-col overflow-hidden bg-surface outline-none sm:inset-auto sm:right-5 sm:bottom-5 sm:h-[520px] sm:max-h-[calc(100dvh-40px)] sm:w-[360px] sm:rounded-card-sm sm:border sm:border-line sm:shadow-[0_18px_50px_-12px_rgba(40,30,120,0.40)] print:hidden"
    >
      <header className="flex items-center gap-2.5 border-b border-line px-4 py-3">
        <Bleep mood={mood} size={40} />
        <div className="min-w-0 flex-1">
          <h2 className="text-lg leading-tight font-extrabold">Bleep</h2>
          <p className="text-[13px] text-ink-2">{t.tagline}</p>
        </div>
        <button
          type="button"
          aria-label={t.export}
          title={t.export}
          onClick={exportChat}
          disabled={exporting}
          aria-busy={exporting}
          className="grid size-9 place-items-center rounded-full hover:bg-soft focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:opacity-50"
        >
          <Download className="size-5" />
        </button>
        <button
          type="button"
          aria-label={t.close}
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
            {t.empty}
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
              {turn.offer?.looking && (
                <LookFollow incidentId={turn.offer.looking} onChange={scrollDown} />
              )}
            </li>
          );
        })}
        {sending && (
          <li className="mr-auto rounded-[18px] rounded-bl-md border border-line bg-surface px-3.5 py-2 text-ink-2">
            {t.reading}
          </li>
        )}
      </ol>
      <form
        onSubmit={send}
        className="border-t border-line p-3 pb-[max(env(safe-area-inset-bottom),12px)]"
      >
        {about && (
          <span className="mb-2 inline-flex items-center gap-1.5 rounded-full bg-soft py-1 pr-1 pl-3 text-[13px] font-bold">
            {t.about} <span className="font-mono">{aboutName ?? t.aboutThis}</span>
            <button
              type="button"
              aria-label={aboutName ? t.leaveOut(aboutName) : t.leaveOutThis}
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
            aria-label={t.message}
            placeholder={about ? t.askIncident : t.ask}
            className="min-w-0 flex-1 bg-transparent text-[14.5px] outline-none placeholder:text-ink-2"
          />
          <button
            type="submit"
            aria-label={t.send}
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
