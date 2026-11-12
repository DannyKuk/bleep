"use client";

import {
  envFields,
  type Field,
  type PanelSecrets,
  SecretRows,
  SectionForm,
} from "@/components/settings/section-form";
import { useState } from "react";

import { type Messages, useT } from "@/lib/i18n";
import type { SettingsUpdate, SettingsView } from "@/lib/settings-types";

export const MODEL_KEYS = ["OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "LOCAL_BASE_URL"] as const;

const KEY_OF = {
  openrouter: "OPENROUTER_API_KEY",
  anthropic: "ANTHROPIC_API_KEY",
  local: "LOCAL_BASE_URL",
} as const;

const hosted = (draft: Record<string, string>) => draft.provider !== "local";
const local = (draft: Record<string, string>) => draft.provider === "local";

const fields = (t: Messages["settings"]["models"]): Field[] => [
  {
    name: "provider",
    label: t.provider,
    env: "LLM_PROVIDER",
    options: [
      { value: "openrouter", label: "OpenRouter" },
      { value: "anthropic", label: "Anthropic" },
      { value: "local", label: t.local },
    ],
  },
  { name: "model", label: t.model, env: "LLM_MODEL", visibleWhen: hosted },
  {
    name: "cheap_model",
    label: t.cheapModel,
    env: "LLM_CHEAP_MODEL",
    hint: t.cheapHint,
    visibleWhen: hosted,
  },
  { name: "local_model", label: t.localModel, env: "LOCAL_MODEL", visibleWhen: local },
];

/**
 * Which models Bleep calls, and the key and address it reaches them with. A change applies at
 * the next model call. Only the fields and the key or address of the chosen provider show. A field the environment sets wins and has no input (#372).
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
  const [provider, setProvider] = useState<keyof typeof KEY_OF>(view.models.provider);
  return (
    <div className="flex flex-col gap-4">
      <SectionForm
        groups={[{ fields: fields(t) }]}
        values={{ ...view.models }}
        overrides={{ ...view.overrides.models }}
        fromEnv={envFields(view.from_env, "models")}
        follow={{ on: "provider", defaults: view.model_defaults }}
        onDraft={(draft) => setProvider(draft.provider as keyof typeof KEY_OF)}
        what={t.save}
        onSave={(models) => onSave({ models })}
      />
      <SecretRows secrets={secrets} names={[KEY_OF[provider]]} />
    </div>
  );
}
