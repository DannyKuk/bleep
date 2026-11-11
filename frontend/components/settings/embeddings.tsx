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
import { type Messages, useT } from "@/lib/i18n";
import type { SettingsUpdate, SettingsView } from "@/lib/settings-types";

export const EMBEDDING_KEYS = ["EMBEDDINGS_BASE_URL", "EMBEDDINGS_API_KEY"] as const;

type EmbeddingMessages = Messages["settings"]["embeddings"];

const fields = (t: EmbeddingMessages): Field[] => [
  {
    name: "provider",
    label: t.embedder,
    env: "EMBEDDINGS_PROVIDER",
    options: [
      { value: "local", label: t.local },
      { value: "openai", label: t.openai },
    ],
  },
  { name: "model", label: t.model, env: "EMBEDDINGS_MODEL", hint: t.modelHint },
  {
    name: "min_score",
    label: t.minScore,
    env: "EMBEDDINGS_MIN_SCORE",
    number: true,
    hint: t.minScoreHint,
  },
];

/** What a save of this change means, said before it goes: when it applies and what it re-embeds. */
export function embeddingsWarning(
  { changed, draft, reset }: Change,
  t: Pick<EmbeddingMessages, "reembed" | "scoreOnly">,
): string {
  const embedderChanged =
    reset ||
    changed.includes("provider") ||
    (changed.includes("model") && draft.provider === "openai");
  return embedderChanged ? t.reembed : t.scoreOnly;
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
  const t = useT().settings.embeddings;
  const { provider, model, min_score } = view.embeddings;
  const keys = provider === "openai" ? EMBEDDING_KEYS : EMBEDDING_KEYS.slice(0, 1);
  return (
    <div className="flex flex-col gap-4">
      {view.restart_needed.includes("embeddings") && <RestartNote>{t.restart}</RestartNote>}
      <SectionForm
        groups={[{ fields: fields(t) }]}
        values={{
          provider,
          model: model ?? "",
          min_score: min_score === null ? "" : String(min_score),
        }}
        overrides={{ ...view.overrides.embeddings }}
        fromEnv={envFields(view.from_env, "embeddings")}
        what={t.save}
        confirm={(change) => embeddingsWarning(change, t)}
        confirmLabel={t.saveNext}
        onSave={(embeddings) => onSave({ embeddings })}
      />
      <SecretRows secrets={secrets} names={keys} />
    </div>
  );
}
