"use client";

import { Loader2, RotateCcw, Save } from "lucide-react";
import { useId, useState } from "react";

import { type SectionFormProps, useSectionSave } from "@/components/dev/models";
import { Failed } from "@/components/dev/status";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { Budgets } from "@/lib/api-types";

type Key = keyof Budgets;

/** Each budget with the bounds `BudgetOverrides` enforces (schemas/settings.py). */
export const FIELDS: { key: Key; label: string; min: number; max: number; step: number }[] = [
  {
    key: "investigate_tool_calls",
    label: "Tool calls per Investigate round",
    min: 1,
    max: 50,
    step: 1,
  },
  { key: "low_confidence", label: "Low-confidence line", min: 0.1, max: 0.95, step: 0.05 },
  { key: "verify_loops", label: "Verify loops back to Investigate", min: 0, max: 5, step: 1 },
  { key: "replans", label: "Trips back to Propose per run", min: 0, max: 10, step: 1 },
  { key: "incident_tokens", label: "Tokens per incident", min: 1_000, max: 2_000_000, step: 1_000 },
  { key: "daily_cost_usd", label: "Daily cost cap (USD)", min: 0, max: 100, step: 0.01 },
  {
    key: "approval_reminder_min",
    label: "Approval reminder (minutes)",
    min: 5,
    max: 1_440,
    step: 1,
  },
];

const asText = (budgets: Budgets) =>
  Object.fromEntries(FIELDS.map(({ key }) => [key, String(budgets[key])])) as Record<Key, string>;

/** The limits that keep an incident and a day from running away; in force from the next node. */
export function BudgetsForm(props: SectionFormProps) {
  const { view } = props;
  const [values, setValues] = useState(() => asText(view.budgets));
  const {
    saving,
    busy,
    failure,
    setFailure,
    save: put,
  } = useSectionSave("budgets", props, "budgets");
  const id = useId();
  const overridden = Object.keys(view.overrides.budgets ?? {}).length > 0;
  const changed = FIELDS.filter(({ key }) => Number(values[key]) !== view.budgets[key]);

  const save = (body: Partial<Budgets>) => put(body, (saved) => setValues(asText(saved.budgets)));

  function submit() {
    const bad = changed.find(
      ({ key, min, max }) =>
        values[key].trim() === "" || !(Number(values[key]) >= min && Number(values[key]) <= max),
    );
    if (bad) {
      setFailure(`${bad.label} must be a number from ${bad.min} to ${bad.max}.`);
      return;
    }
    const edits = Object.fromEntries(changed.map(({ key }) => [key, Number(values[key])]));
    save({ ...view.overrides.budgets, ...edits });
  }

  return (
    <form
      noValidate
      className="flex flex-col gap-3"
      onSubmit={(submitted) => {
        submitted.preventDefault();
        submit();
      }}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        {FIELDS.map(({ key, label, min, max, step }) => (
          <div key={key} className="flex flex-col gap-1.5">
            <label htmlFor={`${id}-${key}`} className="text-xs text-muted-foreground">
              {label}
            </label>
            <Input
              id={`${id}-${key}`}
              type="number"
              inputMode="decimal"
              min={min}
              max={max}
              step={step}
              value={values[key]}
              onChange={(edited) => setValues({ ...values, [key]: edited.target.value })}
            />
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" variant="outline" disabled={busy || changed.length === 0}>
          {saving ? <Loader2 aria-hidden className="animate-spin" /> : <Save aria-hidden />}
          Save the budgets
        </Button>
        {overridden && (
          <Button type="button" variant="ghost" disabled={busy} onClick={() => save({})}>
            <RotateCcw aria-hidden />
            Use the defaults
          </Button>
        )}
      </div>
      {failure && <Failed>{failure}</Failed>}
    </form>
  );
}
