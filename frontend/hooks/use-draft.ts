"use client";

import { useState } from "react";

/**
 * A form's draft of `fresh`, the values in force. When `fresh` changes (a save, or the read after
 * a 409), the draft keeps only the fields the person edited from the values it was built from and
 * takes the rest from `fresh`, so an untouched field never carries an old value into a save.
 * `same` says whether two values of a field are the same edit-wise (a number field: "5" and "5.0").
 */
export function useDraft<T extends object>(
  fresh: T,
  same: (a: unknown, b: unknown) => boolean = Object.is,
) {
  const [draft, setDraft] = useState(fresh);
  const [base, setBase] = useState(fresh);
  if (JSON.stringify(base) !== JSON.stringify(fresh)) {
    const was = base as Record<string, unknown>;
    const edited = draft as Record<string, unknown>;
    const now = fresh as Record<string, unknown>;
    setBase(fresh);
    setDraft(
      Object.fromEntries(
        Object.keys(now).map((key) => [key, !same(edited[key], was[key]) ? edited[key] : now[key]]),
      ) as T,
    );
  }
  return [draft, setDraft] as const;
}
