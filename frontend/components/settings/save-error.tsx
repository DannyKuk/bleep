"use client";

import { TriangleAlert } from "lucide-react";
import { useState } from "react";

import { ApiError } from "@/lib/api";

interface PydanticError {
  loc?: (string | number)[];
  msg?: string;
}

/**
 * What a failed save says, one line per problem. A 422 from the settings API is either Bleep's
 * own sentence or pydantic's list of `{loc, msg}`. `labels` maps a field's name to the words the
 * form shows for it; the item's number follows for a field in a list (`URL 2`, `Rule 1`).
 */
export function problemsOf(error: unknown, labels: Record<string, string> = {}): string[] {
  if (!(error instanceof ApiError)) return [error instanceof Error ? error.message : String(error)];
  const detail = (error.body as { detail?: unknown } | null)?.detail;
  if (!Array.isArray(detail)) return [error.detail];
  return (detail as PydanticError[]).map(({ loc = [], msg = "invalid" }) => {
    const message = msg.replace(/^Value error, /, "");
    const at = loc.findLastIndex((part) => typeof part === "string" && part in labels);
    if (at < 0) return message;
    const index = [loc[at + 1], loc[at - 1]].find(
      (part): part is number => typeof part === "number",
    );
    const label = labels[loc[at] as string] + (index === undefined ? "" : ` ${index + 1}`);
    return `${label}: ${message}`;
  });
}

/** The problems of the last save, as an alert; nothing when it went through. */
export function SaveError({ problems }: { problems: string[] }) {
  if (problems.length === 0) return null;
  return (
    <div role="alert" className="flex items-start gap-2 text-xs text-health-down">
      <TriangleAlert aria-hidden className="mt-px size-3.5 shrink-0" />
      <ul className="flex flex-col gap-0.5">
        {problems.map((problem) => (
          <li key={problem}>{problem}</li>
        ))}
      </ul>
    </div>
  );
}

/** A save in flight and the problems of the last one. `run` resolves true when it went through. */
export function useSaving(labels?: Record<string, string>) {
  const [saving, setSaving] = useState(false);
  const [problems, setProblems] = useState<string[]>([]);

  async function run(save: () => Promise<unknown>): Promise<boolean> {
    setSaving(true);
    setProblems([]);
    try {
      await save();
      return true;
    } catch (error) {
      setProblems(problemsOf(error, labels));
      return false;
    } finally {
      setSaving(false);
    }
  }

  return { saving, problems, run };
}
