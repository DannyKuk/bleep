"use client";

import {
  envFields,
  type Field,
  type PanelSecrets,
  SecretRows,
  SectionForm,
} from "@/components/settings/section-form";
import { type Messages, useT } from "@/lib/i18n";
import type { SettingsUpdate, SettingsView } from "@/lib/settings-types";

export const MODEL_KEYS = ["OPENROUTER_API_KEY", "LOCAL_BASE_URL"] as const;

const fields = (t: Messages["settings"]["models"]): Field[] => [
  {
    name: "provider",
    label: t.provider,
    env: "LLM_PROVIDER",
    options: [
      { value: "openrouter", label: "OpenRouter" },
      { value: "local", label: t.local },
    ],
  },
  { name: "model", label: t.model, env: "LLM_MODEL" },
  { name: "cheap_model", label: t.cheapModel, env: "LLM_CHEAP_MODEL", hint: t.cheapHint },
  { name: "local_model", label: t.localModel, env: "LOCAL_MODEL" },
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
  const t = useT().settings.models;
  return (
    <div className="flex flex-col gap-4">
      <SectionForm
        groups={[{ fields: fields(t) }]}
        values={{ ...view.models }}
        overrides={{ ...view.overrides.models }}
        fromEnv={envFields(view.from_env, "models")}
        what={t.save}
        onSave={(models) => onSave({ models })}
      />
      <SecretRows secrets={secrets} names={MODEL_KEYS} />
    </div>
  );
}
