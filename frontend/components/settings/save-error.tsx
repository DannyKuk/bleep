"use client";

import { TriangleAlert } from "lucide-react";
import { useState } from "react";

import { useSaveLock } from "@/components/save-lock";
import { ApiError } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface PydanticError {
  loc?: (string | number)[];
  msg?: string;
}

/**
 * What a failed save says, one line per problem. A 422 from the settings API is either Bleep's
 * own sentence or pydantic's list of `{loc, msg}`. `labels` maps a field's name to the words the
 * form shows for it; the item's number follows for a field in a list (`URL 2`, `Rule 1`). `invalid`
 * stands for a problem that came without a reason.
 */
export function problemsOf(
  error: unknown,
  labels: Record<string, string> = {},
  invalid = "invalid",
): string[] {
  if (!(error instanceof ApiError)) return [error instanceof Error ? error.message : String(error)];
  const detail = (error.body as { detail?: unknown } | null)?.detail;
  if (!Array.isArray(detail)) return [error.detail];
  return (detail as PydanticError[]).map(({ loc = [], msg = invalid }) => {
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

/**
 * This form's save and the problems of its last one. `busy` is any save on the page in flight
 * (the page's `SaveLockProvider`), when this form's buttons wait. `run` resolves true when the
 * save went through, false when it failed or another save held the lock.
 */
export function useSaving(labels?: Record<string, string>) {
  const lock = useSaveLock();
  const { invalid } = useT().settings;
  const [saving, setSaving] = useState(false);
  const [problems, setProblems] = useState<string[]>([]);

  async function run(save: () => Promise<unknown>): Promise<boolean> {
    let saved = false;
    await lock.run(async () => {
      setSaving(true);
      setProblems([]);
      try {
        await save();
        saved = true;
      } catch (error) {
        setProblems(problemsOf(error, labels, invalid));
      } finally {
        setSaving(false);
      }
    });
    return saved;
  }

  return { saving, busy: lock.busy, problems, run };
}
