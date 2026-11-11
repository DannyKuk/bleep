"use client";

import { Loader2 } from "lucide-react";
import { useState } from "react";

import { SaveError, useSaving } from "@/components/settings/save-error";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useT } from "@/lib/i18n";
import type { Thresholds } from "@/lib/settings-types";

type Name = keyof Thresholds;

/** Every threshold with its unit, in the order the form shows them; the words are in the messages. */
export const THRESHOLDS: { name: Name; unit?: string }[] = [
  { name: "container_grace_s", unit: "s" },
  { name: "repeated_deaths" },
  { name: "repeated_window_s", unit: "s" },
  { name: "url_failures_to_open" },
  { name: "url_slow_ms", unit: "ms" },
  { name: "disk_percent", unit: "%" },
  { name: "memory_available_percent_min", unit: "%" },
  { name: "memory_polls" },
  { name: "load_per_cpu" },
  { name: "load_polls" },
  { name: "host_unreachable_s", unit: "s" },
  { name: "debounce_s", unit: "s" },
  { name: "fold_window_h", unit: "h" },
];

const asDraft = (values: Thresholds) =>
  Object.fromEntries(THRESHOLDS.map(({ name }) => [name, String(values[name])])) as Record<
    Name,
    string
  >;

/** A typed value as a number; anything else goes as typed, for the API to refuse with why. */
const parsed = (text: string): number => {
  const value = Number(text.trim());
  return (text.trim() !== "" && Number.isFinite(value) ? value : text) as number;
};

/**
 * When a signal becomes an incident. Saving sends the changed values with the ones already
 * changed here, so the section's other overrides stay; the API checks every bound.
 */
export function ThresholdsForm({
  values,
  overrides,
  onSave,
}: {
  values: Thresholds;
  overrides: Partial<Thresholds>;
  onSave: (thresholds: Partial<Thresholds>) => Promise<unknown>;
}) {
  const [draft, setDraft] = useState(() => asDraft(values));
  const { thresholds: t, cancel, section } = useT().settings;
  const { saving, busy, problems, run } = useSaving(t.labels);
  const changed = THRESHOLDS.filter(({ name }) => draft[name].trim() !== String(values[name]));
  const overridden = Object.keys(overrides).length > 0;

  function save() {
    const update: Partial<Thresholds> = { ...overrides };
    for (const { name } of changed) update[name] = parsed(draft[name]);
    void run(() => onSave(update));
  }

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(submitted) => {
        submitted.preventDefault();
        save();
      }}
    >
      <div className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
        {THRESHOLDS.map(({ name, unit }) => (
          <label key={name} className="flex items-center justify-between gap-3 text-sm">
            <span className="min-w-0">
              {t.labels[name]}
              {name in overrides && (
                <span className="ml-1.5 text-xs text-muted-foreground">{t.changedHere}</span>
              )}
            </span>
            <span className="flex shrink-0 items-center gap-1.5">
              <Input
                name={name}
                inputMode="decimal"
                value={draft[name]}
                className="w-24 text-right"
                onChange={(typed) => setDraft({ ...draft, [name]: typed.target.value })}
              />
              <span className="w-6 text-xs text-muted-foreground">{unit}</span>
            </span>
          </label>
        ))}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button wrap type="submit" size="sm" disabled={busy || changed.length === 0}>
          {saving && <Loader2 aria-hidden className="animate-spin" />}
          {t.save}
        </Button>
        {changed.length > 0 && (
          <Button
            wrap
            type="button"
            size="sm"
            variant="ghost"
            disabled={saving}
            onClick={() => setDraft(asDraft(values))}
          >
            {cancel}
          </Button>
        )}
        {overridden && (
          <Button
            wrap
            type="button"
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => void run(() => onSave({}))}
          >
            {section.defaults}
          </Button>
        )}
      </div>
      <SaveError problems={problems} />
    </form>
  );
}
