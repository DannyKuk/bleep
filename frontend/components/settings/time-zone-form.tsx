"use client";

import { Loader2 } from "lucide-react";
import { useId, useState } from "react";

import { SaveError, useSaving } from "@/components/settings/save-error";
import { Button } from "@/components/ui/button";
import type { GeneralOverrides } from "@/lib/settings-types";
import { browserZone } from "@/lib/zone";

const LABELS = { timezone: "Time zone" };

/** Every zone the browser knows, its own first; `kept` too when the browser does not list it. */
export function zoneChoices(own: string, kept: string | null): string[] {
  const known = Intl.supportedValuesOf("timeZone");
  return [...new Set([own, ...(kept ? [kept] : []), "UTC", ...known])];
}

/**
 * Bleep's time zone: Telegram's times and every time on the dashboard are shown in it. Every
 * time is stored in UTC, so a change shows every time anew and changes nothing stored.
 */
export function TimeZoneForm({
  timezone,
  chosen,
  onSave,
}: {
  /** In force: the one chosen here, else BLEEP_TIMEZONE; null for none. */
  timezone: string | null;
  /** The one chosen here, if any. */
  chosen: string | undefined;
  onSave: (general: GeneralOverrides) => Promise<unknown>;
}) {
  const id = useId();
  const own = browserZone();
  const [draft, setDraft] = useState(timezone ?? own);
  const { saving, busy, problems, run } = useSaving(LABELS);
  const fromEnv = timezone !== null && chosen === undefined;

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(submitted) => {
        submitted.preventDefault();
        void run(() => onSave({ timezone: draft }));
      }}
    >
      <div className="flex flex-col gap-1.5">
        <label htmlFor={`${id}-zone`} className="text-sm">
          Show every time in
        </label>
        <select
          id={`${id}-zone`}
          value={draft}
          onChange={(changed) => setDraft(changed.target.value)}
          className="h-11 w-full max-w-sm rounded-field border border-line bg-surface px-3 text-[15px] focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          {zoneChoices(own, timezone).map((zone) => (
            <option key={zone} value={zone}>
              {zone === own ? `${zone} (this browser)` : zone}
            </option>
          ))}
        </select>
      </div>
      <p className="text-xs text-muted-foreground">
        {timezone === null
          ? "None chosen yet: the dashboard shows this browser's time zone, and Telegram shows UTC and says so."
          : fromEnv
            ? `${timezone} comes from BLEEP_TIMEZONE in .env until one is chosen here.`
            : `Telegram and the dashboard show every time in ${timezone}.`}{" "}
        Times are kept in UTC, so a change shows every time anew.
      </p>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" size="sm" disabled={busy || draft === chosen}>
          {saving && <Loader2 aria-hidden className="animate-spin" />}
          Save time zone
        </Button>
        {chosen !== undefined && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={() => void run(() => onSave({}))}
          >
            Clear the choice
          </Button>
        )}
      </div>
      <SaveError problems={problems} />
    </form>
  );
}
