"use client";

import { Check, Loader2, type LucideIcon, MessageSquare, TriangleAlert, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { isRefused } from "@/components/incident/headline";
import { useKeyboardInset } from "@/components/incident/use-phone";
import { useReportPinnedBar } from "@/components/pinned-bar";
import { useTimeZone } from "@/components/time-zone";
import { Button } from "@/components/ui/button";
import { ApiError, apiFetch } from "@/lib/api";
import type {
  ApprovalView,
  Decision,
  DecisionBody,
  DecisionKind,
  IncidentStatus,
} from "@/lib/api-types";
import { CHANNEL, formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Zone } from "@/lib/zone";

const MAX_INSTRUCTION = 2000;

// What a yes has led to so far. Only `resolved` says fixed: the run reaches it once its checks
// passed.
const AFTER_YES: Partial<Record<IncidentStatus, { text: string; icon: LucideIcon; tone: string }>> =
  {
    executing: {
      text: "Running the plan.",
      icon: Loader2,
      tone: "text-warn motion-safe:animate-spin",
    },
    verifying: {
      text: "Checking it worked.",
      icon: Loader2,
      tone: "text-warn motion-safe:animate-spin",
    },
    resolved: { text: "Fixed and checked.", icon: Check, tone: "text-ok" },
  };

interface AnswerLine {
  text: string;
  icon: LucideIcon;
  tone: string;
}

/** The one line that replaces the buttons once the approval is answered: who answered, where and
 * when, and what it led to. */
function answerLine(
  decision: Decision,
  refused: boolean,
  status: IncidentStatus | undefined,
  nothingRanBefore: boolean,
  zone: Zone,
): AnswerLine {
  const where = `on ${CHANNEL[decision.via]}, ${formatDateTime(decision.at, zone)}`;
  if (decision.kind === "approve") {
    const after = status ? AFTER_YES[status] : undefined;
    return {
      text: `You said yes ${where}.${after ? ` ${after.text}` : ""}`,
      icon: after?.icon ?? Check,
      tone: after?.tone ?? "text-ink-2",
    };
  }
  if (decision.kind === "reject") {
    // An earlier plan of the same incident may have run before this one was put to the human.
    const nothing = nothingRanBefore ? "Nothing ran." : "This plan did not run.";
    const who = refused ? "You let it go" : "You said no";
    return { text: `${who} ${where}. ${nothing}`, icon: X, tone: "text-ink-2" };
  }
  return {
    text: `You told me ${where}: “${decision.text ?? ""}”. I am planning again.`,
    icon: MessageSquare,
    tone: "text-accent",
  };
}

/**
 * Approve ("Yes, go ahead"), reject ("No, leave it") or instruct one approval. A plan Bleep's
 * own checks refused (`isRefused`, a plan stored before the flag included) has no "Yes, go
 * ahead", and the reject reads "Let it go". The request names the approval it answers, so a stale
 * page cannot approve a plan it never showed. After any answer, or a 409 saying another channel
 * answered first, the buttons lock and the outcome stays on screen. Render it with
 * `key={approval.id}` so a new plan gets fresh buttons.
 *
 * `pinned` is the phone layout of the same bar: fixed to the bottom of the screen, above the
 * on-screen keyboard, and once answered it is only the one line saying what the answer led to.
 */
export function DecisionBar({
  incidentId,
  approval,
  onDecided,
  pinned = false,
  status,
  nothingRanBefore = false,
}: {
  incidentId: string;
  approval: ApprovalView;
  /** Called after the backend recorded this answer or refused it with 409, to reload the incident. */
  onDecided: () => void;
  pinned?: boolean;
  /** The incident's status, for what a yes has led to (running, checking, fixed and checked). */
  status?: IncidentStatus;
  /** Known that no step of any plan of this incident ran, so a no may say "Nothing ran."; else
   * it says only that this plan did not run. */
  nothingRanBefore?: boolean;
}) {
  const [sending, setSending] = useState<DecisionKind | null>(null);
  const zone = useTimeZone();
  const [mine, setMine] = useState<Decision | null>(null);
  const [conflict, setConflict] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [instructing, setInstructing] = useState(false);
  const [text, setText] = useState("");
  const textId = useId();

  const refused = isRefused(approval.proposal);
  const decision = approval.decision ?? mine;
  const locked = decision !== null || conflict !== null || sending !== null;

  async function decide(kind: DecisionKind) {
    const body: DecisionBody = { approval_id: approval.id, kind };
    if (kind === "instruct") body.text = text.trim();
    setSending(kind);
    setFailure(null);
    try {
      const recorded = await apiFetch<Decision>(
        `/api/incidents/${encodeURIComponent(incidentId)}/decision`,
        { method: "POST", body: JSON.stringify(body) },
      );
      setMine(recorded);
      setInstructing(false);
      onDecided();
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setConflict(error.detail);
        setInstructing(false);
        onDecided();
      } else {
        setFailure(error instanceof Error ? error.message : String(error));
      }
    } finally {
      setSending(null);
    }
  }

  const keyboard = useKeyboardInset(pinned);
  const barRef = useRef<HTMLDivElement>(null);
  useReportPinnedBar(barRef, pinned, keyboard);
  const answered = decision !== null || conflict !== null;
  // The pinned bar drops its buttons once answered; focus moves to the line that replaced them
  // instead of falling back to the page, when the answer was given here.
  const answeredHere = pinned && (mine !== null || conflict !== null);
  const answerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (answeredHere) answerRef.current?.focus();
  }, [answeredHere]);

  const spinner = (kind: DecisionKind) =>
    sending === kind ? <Loader2 aria-hidden className="motion-safe:animate-spin" /> : null;

  const yes = !refused && (
    <Button
      className={cn(
        "font-extrabold",
        pinned ? "h-14 w-full text-lg [&_svg]:size-5" : "h-12 px-[22px] text-base",
      )}
      disabled={locked}
      onClick={() => void decide("approve")}
    >
      {spinner("approve") ?? <Check aria-hidden />}
      Yes, go ahead
    </Button>
  );
  const no = (
    <Button
      variant="outline"
      className={cn(
        "h-12 font-extrabold",
        pinned ? "flex-1 border-0 bg-soft px-3" : "border-[1.5px] px-[22px] text-base",
      )}
      disabled={locked}
      onClick={() => void decide("reject")}
    >
      {spinner("reject") ?? <X aria-hidden />}
      {refused ? "Let it go" : "No, leave it"}
    </Button>
  );
  const tell = (
    <Button
      variant="ghost"
      className={cn("h-12 bg-soft font-extrabold", pinned ? "flex-1 px-3" : "px-[22px] text-base")}
      disabled={locked}
      aria-expanded={instructing}
      // The phone's button is too narrow for the whole phrase; its name stays the same.
      aria-label={pinned ? "Tell Bleep something" : undefined}
      onClick={() => setInstructing((open) => !open)}
    >
      <MessageSquare aria-hidden />
      {pinned ? "Tell Bleep" : "Tell Bleep something"}
    </Button>
  );

  const form = instructing && !locked && (
    <form
      className="flex flex-col gap-2"
      onSubmit={(submitted) => {
        submitted.preventDefault();
        if (text.trim()) void decide("instruct");
      }}
    >
      <label htmlFor={textId} className="text-sm font-bold text-ink-2">
        I will plan again with this in mind. Nothing runs until you say yes to a plan.
      </label>
      <textarea
        id={textId}
        value={text}
        maxLength={MAX_INSTRUCTION}
        rows={3}
        autoFocus={pinned}
        onChange={(changed) => setText(changed.target.value)}
        // 16 px at least, or iOS zooms the page in when the box gets focus.
        className="rounded-row border-[1.5px] border-line bg-bg px-4 py-3 text-base focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
      />
      <div className="flex gap-2">
        <Button
          type="submit"
          className={cn("bg-ink text-bg hover:bg-ink/90", pinned && "h-[50px] flex-1 text-base")}
          disabled={!text.trim()}
        >
          Send
        </Button>
        <Button
          variant="ghost"
          type="button"
          className={cn("text-ink-2", pinned && "h-[50px] text-base")}
          onClick={() => setInstructing(false)}
        >
          Cancel
        </Button>
      </div>
    </form>
  );

  const sendingLine = sending === "instruct" && (
    <p className="flex items-center gap-1.5 text-sm text-ink-2">
      <Loader2 aria-hidden className="size-3.5 motion-safe:animate-spin" />
      Sending
    </p>
  );
  const conflictLine = conflict && (
    <p role="alert" className="flex items-center gap-1.5 text-[15px] font-bold text-warn">
      <TriangleAlert aria-hidden className="size-4 shrink-0" />
      {conflict}
    </p>
  );
  const failureLine = failure && (
    <p role="alert" className="flex items-center gap-1.5 text-[15px] font-bold text-down">
      <TriangleAlert aria-hidden className="size-4 shrink-0" />
      Not sent: {failure}
    </p>
  );
  const answer = decision && answerLine(decision, refused, status, nothingRanBefore, zone);
  const answerText = answer && (
    <p
      role="status"
      className={cn("flex items-center gap-2", pinned ? "text-base font-extrabold" : "text-[15px]")}
    >
      <answer.icon aria-hidden className={cn("size-4 shrink-0", pinned && "size-5", answer.tone)} />
      <span>{answer.text}</span>
    </p>
  );

  if (!pinned) {
    return (
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap gap-2.5">
          {yes}
          {no}
          {tell}
        </div>
        {form}
        {sendingLine}
        {conflictLine}
        {failureLine}
        {answerText}
      </div>
    );
  }

  return (
    <div
      ref={barRef}
      data-pinned
      role="region"
      aria-label="Your answer"
      style={{ bottom: keyboard }}
      className="fixed inset-x-0 bottom-0 z-40 flex flex-col gap-2.5 rounded-t-[26px] bg-surface pt-3 pr-[max(env(safe-area-inset-right),16px)] pb-[max(env(safe-area-inset-bottom),16px)] pl-[max(env(safe-area-inset-left),16px)] shadow-[0_-6px_24px_rgba(20,19,42,0.08)] dark:shadow-[0_-6px_24px_rgba(0,0,0,0.35)] print:hidden"
    >
      {answered ? (
        <div
          ref={answerRef}
          tabIndex={-1}
          className="flex min-h-14 flex-col justify-center gap-1 rounded-row focus-visible:outline-none"
        >
          {conflictLine}
          {answerText}
        </div>
      ) : (
        <>
          {failureLine}
          {form || (
            <>
              {yes}
              <div className="flex gap-2.5">
                {no}
                {tell}
              </div>
            </>
          )}
          {sendingLine}
        </>
      )}
    </div>
  );
}
