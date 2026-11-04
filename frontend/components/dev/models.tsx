"use client";

import { Loader2, RotateCcw, Save } from "lucide-react";
import { useId, useState } from "react";

import { Failed, why } from "@/components/dev/status";
import { useSaveLock } from "@/components/save-lock";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { DevSettingsView, ModelChoice } from "@/lib/api-types";
import { isStale, putSettings, SETTINGS_CHANGED } from "@/lib/settings-api";

const NAMES = [
  { key: "model", label: "Model" },
  { key: "cheap_model", label: "Cheap model (one-line recovery explanations)" },
  { key: "local_model", label: "Local model" },
] as const;

/** The props of a form that saves one section of the settings. */
export interface SectionFormProps {
  view: DevSettingsView;
  onSaved: (view: DevSettingsView) => void;
  /** The save was refused because the settings changed elsewhere: read them again. */
  onStale: () => Promise<void>;
}

/**
 * Saves one section of the settings, built from `view`: `{}` clears its overrides, so the layer
 * below shows. One save at a time on the panel; a stale one reads the settings again and says
 * so, and is never retried, since its body would put back what changed elsewhere.
 */
export function useSectionSave(
  section: "models" | "budgets",
  { view, onSaved, onStale }: SectionFormProps,
  what: string,
) {
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
          setFailure(SETTINGS_CHANGED);
          await onStale().catch(() => undefined);
        } else {
          setFailure(`Could not save the ${what}: ${why(error)}`);
        }
      } finally {
        setSaving(false);
      }
    });
  }

  return { saving, busy: lock.busy, failure, setFailure, save };
}

/**
 * Which models Bleep calls. A saved choice overrides the env's (`LLM_PROVIDER`, `LLM_MODEL`, ...);
 * the local model's base URL and every key stay in the env.
 */
export function ModelsForm(props: SectionFormProps) {
  const { view } = props;
  const [choice, setChoice] = useState<ModelChoice>(view.models);
  const { saving, busy, failure, save: put } = useSectionSave("models", props, "models");
  const id = useId();
  const overridden = Object.keys(view.overrides.models ?? {}).length > 0;
  const changed = (Object.keys(choice) as (keyof ModelChoice)[]).filter(
    (key) => choice[key] !== view.models[key],
  );

  const save = (body: Partial<ModelChoice>) => put(body, (saved) => setChoice(saved.models));

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(submitted) => {
        submitted.preventDefault();
        const edits = Object.fromEntries(changed.map((key) => [key, choice[key].trim()]));
        save({ ...view.overrides.models, ...edits });
      }}
    >
      <div className="flex flex-col gap-1.5">
        <label htmlFor={`${id}-provider`} className="text-xs text-muted-foreground">
          Provider
        </label>
        <select
          id={`${id}-provider`}
          value={choice.provider}
          onChange={(changed) =>
            setChoice({ ...choice, provider: changed.target.value as ModelChoice["provider"] })
          }
          className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          <option value="openrouter">OpenRouter</option>
          <option value="local">Local (OpenAI-compatible)</option>
        </select>
      </div>
      {NAMES.map(({ key, label }) => (
        <div key={key} className="flex flex-col gap-1.5">
          <label htmlFor={`${id}-${key}`} className="text-xs text-muted-foreground">
            {label}
          </label>
          <Input
            id={`${id}-${key}`}
            value={choice[key]}
            maxLength={200}
            className="font-mono"
            onChange={(changed) => setChoice({ ...choice, [key]: changed.target.value })}
          />
        </div>
      ))}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" variant="outline" disabled={busy || changed.length === 0}>
          {saving ? <Loader2 aria-hidden className="animate-spin" /> : <Save aria-hidden />}
          Save the models
        </Button>
        {overridden && (
          <Button type="button" variant="ghost" disabled={busy} onClick={() => save({})}>
            <RotateCcw aria-hidden />
            Use the env&apos;s models
          </Button>
        )}
      </div>
      {failure && <Failed>{failure}</Failed>}
    </form>
  );
}
