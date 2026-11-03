"use client";

import { Loader2 } from "lucide-react";
import { useState } from "react";

import { SaveError, useSaving } from "@/components/settings/save-error";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { Thresholds } from "@/lib/settings-types";

type Name = keyof Thresholds;

/** Every threshold with what it decides and its unit, in the order the form shows them. */
export const THRESHOLDS: { name: Name; label: string; unit?: string }[] = [
  { name: "container_grace_s", label: "Wait before a dead container opens an incident", unit: "s" },
  { name: "repeated_deaths", label: "Deaths that make a crash loop" },
  { name: "repeated_window_s", label: "Counted within", unit: "s" },
  { name: "url_failures_to_open", label: "Failed URL checks in a row before an incident" },
  { name: "url_slow_ms", label: "A URL slower than this counts as failed", unit: "ms" },
  { name: "disk_percent", label: "Disk used at or above", unit: "%" },
  { name: "memory_available_percent_min", label: "Memory available below", unit: "%" },
  { name: "memory_polls", label: "Memory low for this many probes" },
  { name: "load_per_cpu", label: "Load per CPU above" },
  { name: "load_polls", label: "Load high for this many probes" },
  { name: "host_unreachable_s", label: "Host unreachable before an incident", unit: "s" },
  { name: "debounce_s", label: "Signals within this time join one incident", unit: "s" },
];

const LABELS = Object.fromEntries(THRESHOLDS.map(({ name, label }) => [name, label]));

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
  const { saving, problems, run } = useSaving(LABELS);
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
        {THRESHOLDS.map(({ name, label, unit }) => (
          <label key={name} className="flex items-center justify-between gap-3 text-sm">
            <span className="min-w-0">
              {label}
              {name in overrides && (
                <span className="ml-1.5 text-xs text-muted-foreground">(changed here)</span>
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
        <Button type="submit" size="sm" disabled={saving || changed.length === 0}>
          {saving && <Loader2 aria-hidden className="animate-spin" />}
          Save thresholds
        </Button>
        {changed.length > 0 && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={saving}
            onClick={() => setDraft(asDraft(values))}
          >
            Cancel
          </Button>
        )}
        {overridden && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={saving}
            onClick={() => void run(() => onSave({}))}
          >
            Back to the defaults
          </Button>
        )}
      </div>
      <SaveError problems={problems} />
    </form>
  );
}
