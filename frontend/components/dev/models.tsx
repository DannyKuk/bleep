"use client";

import { Loader2, RotateCcw, Save } from "lucide-react";
import { useId, useState } from "react";

import { Failed, why } from "@/components/dev/status";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { apiFetch } from "@/lib/api";
import type { DevSettingsView, ModelChoice } from "@/lib/api-types";

const NAMES = [
  { key: "model", label: "Model" },
  { key: "cheap_model", label: "Cheap model (one-line recovery explanations)" },
  { key: "local_model", label: "Local model" },
] as const;

/** Saves one section of the settings: `{}` clears its overrides, so the layer below shows. */
export async function putSection(
  section: "models" | "budgets",
  body: object,
): Promise<DevSettingsView> {
  return apiFetch<DevSettingsView>("/api/settings", {
    method: "PUT",
    body: JSON.stringify({ [section]: body }),
  });
}

/**
 * Which models Bleep calls. A saved choice overrides the env's (`LLM_PROVIDER`, `LLM_MODEL`, ...);
 * the local model's base URL and every key stay in the env.
 */
export function ModelsForm({
  view,
  onSaved,
}: {
  view: DevSettingsView;
  onSaved: (view: DevSettingsView) => void;
}) {
  const [choice, setChoice] = useState<ModelChoice>(view.models);
  const [saving, setSaving] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const id = useId();
  const overridden = Object.keys(view.overrides.models ?? {}).length > 0;
  const changed = (Object.keys(choice) as (keyof ModelChoice)[]).filter(
    (key) => choice[key] !== view.models[key],
  );

  async function save(body: Partial<ModelChoice>) {
    setSaving(true);
    setFailure(null);
    try {
      const saved = await putSection("models", body);
      setChoice(saved.models);
      onSaved(saved);
    } catch (error) {
      setFailure(`Could not save the models: ${why(error)}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(submitted) => {
        submitted.preventDefault();
        const edits = Object.fromEntries(changed.map((key) => [key, choice[key].trim()]));
        void save({ ...view.overrides.models, ...edits });
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
        <Button type="submit" variant="outline" disabled={saving || changed.length === 0}>
          {saving ? <Loader2 aria-hidden className="animate-spin" /> : <Save aria-hidden />}
          Save the models
        </Button>
        {overridden && (
          <Button type="button" variant="ghost" disabled={saving} onClick={() => void save({})}>
            <RotateCcw aria-hidden />
            Use the env&apos;s models
          </Button>
        )}
      </div>
      {failure && <Failed>{failure}</Failed>}
    </form>
  );
}
