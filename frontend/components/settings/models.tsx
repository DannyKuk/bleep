"use client";

import {
  envFields,
  type Field,
  type PanelSecrets,
  SecretRows,
  SectionForm,
} from "@/components/settings/section-form";
import type { SettingsUpdate, SettingsView } from "@/lib/settings-types";

export const MODEL_KEYS = ["OPENROUTER_API_KEY", "LOCAL_BASE_URL"] as const;

const FIELDS: Field[] = [
  {
    name: "provider",
    label: "Provider",
    env: "LLM_PROVIDER",
    options: [
      { value: "openrouter", label: "OpenRouter" },
      { value: "local", label: "Local (OpenAI-compatible server)" },
    ],
  },
  { name: "model", label: "Model", env: "LLM_MODEL" },
  {
    name: "cheap_model",
    label: "Cheap model",
    env: "LLM_CHEAP_MODEL",
    hint: "Writes the one-line recovery explanations.",
  },
  { name: "local_model", label: "Local model", env: "LOCAL_MODEL" },
];

/**
 * Which models Bleep calls, and the key and address it reaches them with. A change applies at
 * the next model call. A field the environment sets wins and has no input (#372).
 */
export function ModelSettings({
  view,
  onSave,
  secrets,
}: {
  view: SettingsView;
  onSave: (update: SettingsUpdate) => Promise<unknown>;
  secrets?: PanelSecrets;
}) {
  return (
    <div className="flex flex-col gap-4">
      <SectionForm
        groups={[{ fields: FIELDS }]}
        values={{ ...view.models }}
        overrides={{ ...view.overrides.models }}
        fromEnv={envFields(view.from_env, "models")}
        what="Save the models"
        onSave={(models) => onSave({ models })}
      />
      <SecretRows secrets={secrets} names={MODEL_KEYS} />
    </div>
  );
}
