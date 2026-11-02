"use client";

import { Check, Loader2, MessageSquare, TriangleAlert, X } from "lucide-react";
import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { ApiError, apiFetch } from "@/lib/api";
import type { ApprovalView, Decision, DecisionBody, DecisionKind } from "@/lib/api-types";
import { CHANNEL, formatDateTime } from "@/lib/format";

const MAX_INSTRUCTION = 2000;

function decidedText(decision: Decision): string {
  const where = CHANNEL[decision.via];
  if (decision.kind === "approve") return `Approved on ${where}`;
  if (decision.kind === "reject") return `Rejected on ${where}`;
  return `Instruction sent from ${where}: “${decision.text ?? ""}”`;
}

/**
 * Approve, reject or instruct one approval. The request names the approval it answers, so a stale
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
    sending === kind ? <Loader2 aria-hidden className="animate-spin" /> : null;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={locked} onClick={() => void decide("approve")}>
          {spinner("approve") ?? <Check aria-hidden />}
          Approve
        </Button>
        <Button size="sm" variant="outline" disabled={locked} onClick={() => void decide("reject")}>
          {spinner("reject") ?? <X aria-hidden />}
          Reject
        </Button>
        <Button
          size="sm"
          variant="ghost"
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
          <label htmlFor={textId} className="text-xs text-muted-foreground">
            Bleep plans again with this in mind. Nothing runs until you approve a plan.
          </label>
          <textarea
            id={textId}
            value={text}
            maxLength={MAX_INSTRUCTION}
            rows={3}
            onChange={(changed) => setText(changed.target.value)}
            className="rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          />
          <div className="flex gap-2">
            <Button size="sm" type="submit" disabled={!text.trim()}>
              Send
            </Button>
            <Button size="sm" variant="ghost" type="button" onClick={() => setInstructing(false)}>
              Cancel
            </Button>
          </div>
        </form>
      )}

      {sending === "instruct" && (
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Loader2 aria-hidden className="size-3.5 animate-spin" />
          Sending
        </p>
      )}
      {conflict && (
        <p role="alert" className="flex items-center gap-1.5 text-sm text-health-degraded">
          <TriangleAlert aria-hidden className="size-4 shrink-0" />
          {conflict}
        </p>
      )}
      {failure && (
        <p role="alert" className="flex items-center gap-1.5 text-sm text-health-down">
          <TriangleAlert aria-hidden className="size-4 shrink-0" />
          Not sent: {failure}
        </p>
      )}
      {decision && (
        <p role="status" className="text-sm text-muted-foreground">
          {decidedText(decision)}, {formatDateTime(decision.at)}
        </p>
      )}
    </div>
  );
}
