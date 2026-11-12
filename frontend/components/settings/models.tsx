"use client";

import { useState } from "react";

import {
  envFields,
  type Field,
  type PanelSecrets,
  SecretRows,
  SectionForm,
} from "@/components/settings/section-form";
import { type Messages, useT } from "@/lib/i18n";
import type { ModelChoice, SettingsUpdate, SettingsView } from "@/lib/settings-types";

export const MODEL_KEYS = ["OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "LOCAL_BASE_URL"] as const;

type Provider = ModelChoice["provider"];

const KEY_OF = {
  openrouter: "OPENROUTER_API_KEY",
  anthropic: "ANTHROPIC_API_KEY",
  local: "LOCAL_BASE_URL",
} as const satisfies Record<Provider, (typeof MODEL_KEYS)[number]>;

const hosted = (draft: Record<string, string>) => draft.provider !== "local";
const local = (draft: Record<string, string>) => draft.provider === "local";

const fields = (t: Messages["settings"]["models"], examples: string[]): Field[] => [
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
  { name: "model", label: t.model, env: "LLM_MODEL", examples, visibleWhen: hosted },
  {
    name: "cheap_model",
    label: t.cheapModel,
    env: "LLM_CHEAP_MODEL",
    hint: t.cheapHint,
    examples,
    visibleWhen: hosted,
  },
  { name: "local_model", label: t.localModel, env: "LOCAL_MODEL", examples, visibleWhen: local },
];

/**
 * Which models Bleep calls, and the key and address it reaches them with. A change applies at
 * the next model call. Only the fields and the key or address of the provider in the select
 * show, and a model field nobody edited shows the id that runs with that provider, as the view
 * gives it (#517). A field the environment sets wins and has no input (#372).
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
  const [provider, setProvider] = useState<Provider>(view.models.provider);
  const idsFor = (draft: Record<string, string>) => {
    const { model, cheap_model, local_model } = view.models_by_provider[draft.provider as Provider];
    return { model, cheap_model, local_model };
  };
  return (
    <div className="flex flex-col gap-4">
      <SectionForm
        groups={[{ fields: fields(t, view.models_by_provider[provider].examples) }]}
        values={{ ...view.models }}
        overrides={{ ...view.overrides.models }}
        fromEnv={envFields(view.from_env, "models")}
        valuesFor={idsFor}
        onDraft={(draft) => setProvider(draft.provider as Provider)}
        what={t.save}
        onSave={(models) => onSave({ models })}
      />
      <SecretRows secrets={secrets} names={[KEY_OF[provider]]} />
    </div>
  );
}
