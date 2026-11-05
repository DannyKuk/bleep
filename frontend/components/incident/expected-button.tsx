"use client";

import { TriangleAlert } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { ApiError, apiFetch } from "@/lib/api";

/**
 * "This is expected": records what makes the next identical event quiet (`expectation` says what)
 * and closes the incident. A plan waiting for a yes is rejected first; when another channel
 * decided it already, the API says so and nothing is recorded.
 */
export function ExpectedButton({
  incidentId,
  expectation,
  onDone,
}: {
  incidentId: string;
  expectation: string;
  onDone: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function mark() {
    setBusy(true);
    setProblem(null);
    try {
      await apiFetch<{ text: string }>(
        `/api/incidents/${encodeURIComponent(incidentId)}/expected`,
        { method: "POST" },
      );
      onDone();
    } catch (error) {
      setProblem(error instanceof ApiError ? error.detail : String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <Button variant="outline" disabled={busy} onClick={() => void mark()} title={expectation}>
        This is expected
      </Button>
      <p className="max-w-md text-right text-xs text-ink-2">{expectation}</p>
      {problem && (
        <p role="alert" className="flex items-center gap-1 text-xs font-bold text-down">
          <TriangleAlert aria-hidden className="size-3.5" />
          {problem}
        </p>
      )}
    </div>
  );
}
