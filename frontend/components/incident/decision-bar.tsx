"use client";

import { Check, Loader2, MessageSquare, TriangleAlert, X } from "lucide-react";
import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { ApiError, apiFetch } from "@/lib/api";
import type { ApprovalView, Decision, DecisionBody, DecisionKind } from "@/lib/api-types";
import { CHANNEL, formatDateTime } from "@/lib/format";

const MAX_INSTRUCTION = 2000;

function decidedText(decision: Decision, refused: boolean): string {
  const where = CHANNEL[decision.via];
  if (decision.kind === "approve") return `Approved on ${where}`;
  if (decision.kind === "reject") return refused ? `Let go on ${where}` : `Rejected on ${where}`;
  return `Instruction sent from ${where}: “${decision.text ?? ""}”`;
}

/**
 * Approve ("Yes, go ahead"), reject ("No, leave it") or instruct one approval. A plan Bleep's
 * own checks refused has no "Yes, go ahead", and the reject reads "Let it go". The request names the approval it answers, so a stale
 * page cannot approve a plan it never showed. After any answer, or a 409 saying another channel
 * answered first, the buttons lock and the outcome stays on screen. Render it with
 * `key={approval.id}` so a new plan gets fresh buttons.
 */
export function DecisionBar({
  incidentId,
  approval,
  onDecided,
}: {
  incidentId: string;
  approval: ApprovalView;
  /** Called after the backend recorded this answer or refused it with 409, to reload the incident. */
  onDecided: () => void;
}) {
  const [sending, setSending] = useState<DecisionKind | null>(null);
  const [mine, setMine] = useState<Decision | null>(null);
  const [conflict, setConflict] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [instructing, setInstructing] = useState(false);
  const [text, setText] = useState("");
  const textId = useId();

  const refused = approval.proposal.refused === true;
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

  const spinner = (kind: DecisionKind) =>
    sending === kind ? <Loader2 aria-hidden className="motion-safe:animate-spin" /> : null;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-2.5">
        {!refused && (
          <Button
            className="h-12 px-[22px] text-base font-extrabold"
            disabled={locked}
            onClick={() => void decide("approve")}
          >
            {spinner("approve") ?? <Check aria-hidden />}
            Yes, go ahead
          </Button>
        )}
        <Button
          variant="outline"
          className="h-12 border-[1.5px] px-[22px] text-base font-extrabold"
          disabled={locked}
          onClick={() => void decide("reject")}
        >
          {spinner("reject") ?? <X aria-hidden />}
          {refused ? "Let it go" : "No, leave it"}
        </Button>
        <Button
          variant="ghost"
          className="h-12 bg-soft px-[22px] text-base font-extrabold"
          disabled={locked}
          aria-expanded={instructing}
          onClick={() => setInstructing((open) => !open)}
        >
          <MessageSquare aria-hidden />
          Tell Bleep something
        </Button>
      </div>

      {instructing && !locked && (
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
            onChange={(changed) => setText(changed.target.value)}
            className="rounded-row border-[1.5px] border-line bg-bg px-4 py-3 text-base focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          />
          <div className="flex gap-2">
            <Button
              type="submit"
              className="bg-ink text-bg hover:bg-ink/90"
              disabled={!text.trim()}
            >
              Send
            </Button>
            <Button
              variant="ghost"
              type="button"
              className="text-ink-2"
              onClick={() => setInstructing(false)}
            >
              Cancel
            </Button>
          </div>
        </form>
      )}

      {sending === "instruct" && (
        <p className="flex items-center gap-1.5 text-sm text-ink-2">
          <Loader2 aria-hidden className="size-3.5 motion-safe:animate-spin" />
          Sending
        </p>
      )}
      {conflict && (
        <p role="alert" className="flex items-center gap-1.5 text-[15px] font-bold text-warn">
          <TriangleAlert aria-hidden className="size-4 shrink-0" />
          {conflict}
        </p>
      )}
      {failure && (
        <p role="alert" className="flex items-center gap-1.5 text-[15px] font-bold text-down">
          <TriangleAlert aria-hidden className="size-4 shrink-0" />
          Not sent: {failure}
        </p>
      )}
      {decision && (
        <p role="status" className="text-[15px] text-ink-2">
          {decidedText(decision, refused)}, {formatDateTime(decision.at)}
        </p>
      )}
    </div>
  );
}
