"use client";

import { Loader2 } from "lucide-react";
import { useId, useState } from "react";

import { SaveError, useSaving } from "@/components/settings/save-error";
import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";
import type { Language } from "@/lib/settings-types";

/**
 * The language the model writes what the person reads in: reports, diagnoses, plans and the
 * recovery line; Telegram, the chat's own lines (#375) and this dashboard (#376) follow it too.
 * What the code reads (actions, containers, settings, commands) never changes.
 */
export function LanguageForm({
  language,
  onSave,
}: {
  /** In force: the one chosen here, else English. */
  language: Language;
  onSave: (language: Language) => Promise<unknown>;
}) {
  const id = useId();
  const [draft, setDraft] = useState<Language>(language);
  const t = useT().settings.language;
  const { saving, busy, problems, run } = useSaving({ language: t.label });
  const options: { value: Language; label: string }[] = [
    { value: "en", label: t.english },
    { value: "de", label: t.german },
  ];

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(submitted) => {
        submitted.preventDefault();
        void run(() => onSave(draft));
      }}
    >
      <div className="flex flex-col gap-1.5">
        <label htmlFor={`${id}-language`} className="text-sm">
          {t.writesIn}
        </label>
        <select
          id={`${id}-language`}
          value={draft}
          onChange={(changed) => setDraft(changed.target.value as Language)}
          className="h-11 w-full max-w-sm rounded-field border border-line bg-surface px-3 text-[15px] focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>
      <p className="text-xs text-muted-foreground">{t.note(t.name[language])}</p>
      <div className="flex flex-wrap gap-2">
        <Button wrap type="submit" size="sm" disabled={busy || draft === language}>
          {saving && <Loader2 aria-hidden className="animate-spin" />}
          {t.save}
        </Button>
      </div>
      <SaveError problems={problems} />
    </form>
  );
}
