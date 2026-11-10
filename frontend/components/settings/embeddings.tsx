"use client";

import {
  type Change,
  envFields,
  type Field,
  type PanelSecrets,
  RestartNote,
  SecretRows,
  SectionForm,
} from "@/components/settings/section-form";
import type { SettingsUpdate, SettingsView } from "@/lib/settings-types";

export const EMBEDDING_KEYS = ["EMBEDDINGS_BASE_URL", "EMBEDDINGS_API_KEY"] as const;

const FIELDS: Field[] = [
  {
    name: "provider",
    label: "Embedder",
    env: "EMBEDDINGS_PROVIDER",
    options: [
      { value: "local", label: "The local model that ships with Bleep" },
      { value: "openai", label: "An OpenAI-compatible endpoint" },
    ],
  },
  {
    name: "model",
    label: "Endpoint model",
    env: "EMBEDDINGS_MODEL",
    hint: "The model the endpoint serves. The local model ignores it.",
  },
  {
    name: "min_score",
    label: "Recall's minimum score",
    env: "EMBEDDINGS_MIN_SCORE",
    number: true,
    hint: "0 to 1. Past incidents scoring under it are never recalled. Empty: the model's own.",
  },
];

const AT_NEXT_START = "It applies at the next start of Bleep, not at once.";
const RE_EMBED = `${AT_NEXT_START} A new provider or model makes Bleep embed the memory and the knowledge index again then, and until that is done past incidents are left out of recall.`;

/** What a save of this change means, said before it goes: when it applies and what it re-embeds. */
export function embeddingsWarning({ changed, draft, reset }: Change): string {
  const embedderChanged =
    reset ||
    changed.includes("provider") ||
    (changed.includes("model") && draft.provider === "openai");
  return embedderChanged
    ? RE_EMBED
    : `${AT_NEXT_START} A minimum score does not embed anything again.`;
}

/**
 * The embedder behind memory and knowledge search. A change applies at the next start, so a save
 * asks first, and the panel says so while a restart is needed. The endpoint's key is shown only
 * when the embedder in force is an endpoint, since the local model needs none (#372).
 */
export function EmbeddingSettings({
  view,
  onSave,
  secrets,
}: {
  view: SettingsView;
  onSave: (update: SettingsUpdate) => Promise<unknown>;
  secrets?: PanelSecrets;
}) {
  const { provider, model, min_score } = view.embeddings;
  const keys = provider === "openai" ? EMBEDDING_KEYS : EMBEDDING_KEYS.slice(0, 1);
  return (
    <div className="flex flex-col gap-4">
      {view.restart_needed.includes("embeddings") && (
        <RestartNote>
          A restart is needed: the embeddings saved here are in force only after Bleep restarts.
        </RestartNote>
      )}
      <SectionForm
        groups={[{ fields: FIELDS }]}
        values={{
          provider,
          model: model ?? "",
          min_score: min_score === null ? "" : String(min_score),
        }}
        overrides={{ ...view.overrides.embeddings }}
        fromEnv={envFields(view.from_env, "embeddings")}
        what="Save the embeddings"
        confirm={embeddingsWarning}
        confirmLabel="Save for the next start"
        onSave={(embeddings) => onSave({ embeddings })}
      />
      <SecretRows secrets={secrets} names={keys} />
    </div>
  );
}
