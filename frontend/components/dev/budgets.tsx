"use client";

import { Loader2, RotateCcw, Save } from "lucide-react";
import { useId } from "react";

import { type SectionFormProps, useDraft, useSectionSave } from "@/components/dev/section";
import { Failed } from "@/components/dev/status";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { Budgets } from "@/lib/api-types";
import { useT } from "@/lib/i18n";

type Key = keyof Budgets;

/** Each budget with the bounds `BudgetOverrides` enforces (schemas/settings.py); its words are
 * `dev.budgets.labels` in the messages. */
export const FIELDS: { key: Key; min: number; max: number; step: number }[] = [
  { key: "investigate_tool_calls", min: 1, max: 50, step: 1 },
  { key: "low_confidence", min: 0.1, max: 0.95, step: 0.05 },
  { key: "verify_loops", min: 0, max: 5, step: 1 },
  { key: "replans", min: 0, max: 10, step: 1 },
  { key: "incident_tokens", min: 1_000, max: 2_000_000, step: 1_000 },
  { key: "daily_cost_usd", min: 0, max: 100, step: 0.01 },
  { key: "approval_reminder_min", min: 5, max: 1_440, step: 1 },
  { key: "model_timeout_s", min: 10, max: 600, step: 1 },
  { key: "paused_retry_min", min: 1, max: 240, step: 1 },
  { key: "stuck_after_min", min: 5, max: 1_440, step: 1 },
];

const asText = (budgets: Budgets) =>
  Object.fromEntries(FIELDS.map(({ key }) => [key, String(budgets[key])])) as Record<Key, string>;

/** A field's number; empty text is not one, so it never equals a value. */
const numeric = (text: string) => (text.trim() === "" ? NaN : Number(text));
const sameNumber = (a: unknown, b: unknown) => numeric(String(a)) === numeric(String(b));

/** The limits that keep an incident and a day from running away; in force from the next node. */
export function BudgetsForm(props: SectionFormProps) {
  const { view } = props;
  const t = useT().dev.budgets;
  const [values, setValues] = useDraft(asText(view.budgets), sameNumber);
  const {
    saving,
    busy,
    failure,
    setFailure,
    save: put,
  } = useSectionSave("budgets", props, t.saveFailed);
  const id = useId();
  const overridden = Object.keys(view.overrides.budgets ?? {}).length > 0;
  const changed = FIELDS.filter(({ key }) => numeric(values[key]) !== view.budgets[key]);

  const save = (body: Partial<Budgets>) => put(body, (saved) => setValues(asText(saved.budgets)));

  function submit() {
    const bad = changed.find(
      ({ key, min, max }) =>
        values[key].trim() === "" || !(Number(values[key]) >= min && Number(values[key]) <= max),
    );
    if (bad) {
      setFailure(t.mustBe(t.labels[bad.key], bad.min, bad.max));
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
        {FIELDS.map(({ key, min, max, step }) => (
          <div key={key} className="flex flex-col gap-1.5">
            <label htmlFor={`${id}-${key}`} className="text-xs text-muted-foreground">
              {t.labels[key]}
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
        <Button wrap type="submit" variant="outline" disabled={busy || changed.length === 0}>
          {saving ? <Loader2 aria-hidden className="animate-spin" /> : <Save aria-hidden />}
          {t.save}
        </Button>
        {overridden && (
          <Button wrap type="button" variant="ghost" disabled={busy} onClick={() => save({})}>
            <RotateCcw aria-hidden />
            {t.defaults}
          </Button>
        )}
      </div>
      {failure && <Failed>{failure}</Failed>}
    </form>
  );
}
