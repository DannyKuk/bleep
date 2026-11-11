"use client";

import { useState } from "react";

import { why } from "@/components/dev/status";
import { useSaveLock } from "@/components/save-lock";
import type { DevSettingsView } from "@/lib/api-types";
import { useT } from "@/lib/i18n";
import { isStale, putSettings } from "@/lib/settings-api";

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

/** The props of a form that saves one section of the dev panel's settings. */
export interface SectionFormProps {
  view: DevSettingsView;
  onSaved: (view: DevSettingsView) => void;
  /** The save was refused because the settings changed elsewhere: read them again (throws when
   * that read fails). */
  onStale: () => Promise<void>;
}

/**
 * Saves one section of the settings, built from `view`: `{}` clears its overrides, so the layer
 * below shows. One save at a time on the panel; a stale one reads the settings again and says
 * so, and is never retried, since its body would put back what changed elsewhere.
 */
export function useSectionSave(
  section: "budgets",
  { view, onSaved, onStale }: SectionFormProps,
  saveFailed: (message: string) => string,
) {
  const t = useT();
  const lock = useSaveLock();
  const [saving, setSaving] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  function save(body: object, then: (saved: DevSettingsView) => void) {
    void lock.run(async () => {
      setSaving(true);
      setFailure(null);
      try {
        const saved = await putSettings<DevSettingsView>({ [section]: body }, view.version);
        then(saved);
        onSaved(saved);
      } catch (error) {
        if (isStale(error)) {
          try {
            await onStale();
            setFailure(t.dev.readAgain);
          } catch (failed) {
            setFailure(t.settings.rereadFailed(why(failed)));
          }
        } else {
          setFailure(saveFailed(why(error)));
        }
      } finally {
        setSaving(false);
      }
    });
  }

  return { saving, busy: lock.busy, failure, setFailure, save };
}
