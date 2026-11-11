"use client";

import {
  envFields,
  type Group,
  type PanelSecrets,
  RestartNote,
  SecretRows,
  SectionForm,
} from "@/components/settings/section-form";
import { type Messages, useT } from "@/lib/i18n";
import type { SettingsUpdate, SettingsView } from "@/lib/settings-types";

export const TRACING_KEYS = [
  "LANGFUSE_PUBLIC_KEY",
  "LANGFUSE_SECRET_KEY",
  "LANGSMITH_API_KEY",
] as const;

const groups = (t: Messages["settings"]["tracing"]): Group[] => [
  {
    fields: [
      {
        name: "tracing",
        label: t.tracing,
        env: "TRACING",
        options: [
          { value: "off", label: t.off },
          { value: "langfuse", label: "Langfuse" },
          { value: "langsmith", label: "LangSmith" },
        ],
        hint: t.tracingHint,
      },
    ],
  },
  {
    title: "Langfuse",
    fields: [
      {
        name: "langfuse_base_url",
        label: t.baseUrl,
        env: "LANGFUSE_BASE_URL",
        hint: t.addressRule,
      },
      { name: "langfuse_ui_url", label: t.uiUrl, env: "LANGFUSE_UI_URL", hint: t.uiUrlHint },
      { name: "langfuse_project_id", label: t.projectId, env: "LANGFUSE_PROJECT_ID" },
    ],
  },
  {
    title: "LangSmith",
    fields: [
      {
        name: "langsmith_endpoint",
        label: t.endpoint,
        env: "LANGSMITH_ENDPOINT",
        hint: t.endpointHint(t.addressRule),
      },
      { name: "langsmith_project", label: t.project, env: "LANGSMITH_PROJECT" },
    ],
  },
];

/**
 * Where incident runs and chat readings are traced, and the keys sent there. Switching tracing
 * off or to LangSmith, and a change to a LangSmith setting, apply at once; a change to a running
 * Langfuse tracer waits for the next start, which the panel says while it does (#372).
 */
export function TracingSettings({
  view,
  onSave,
  secrets,
}: {
  view: SettingsView;
  onSave: (update: SettingsUpdate) => Promise<unknown>;
  secrets?: PanelSecrets;
}) {
  const t = useT().settings.tracing;
  const shown = groups(t);
  const values = Object.fromEntries(
    shown
      .flatMap((group) => group.fields)
      .map(({ name }) => [name, view.tracing[name as keyof typeof view.tracing] ?? ""]),
  );
  return (
    <div className="flex flex-col gap-4">
      {view.restart_needed.includes("tracing") && <RestartNote>{t.restart}</RestartNote>}
      <SectionForm
        groups={shown}
        values={values}
        overrides={{ ...view.overrides.tracing }}
        fromEnv={envFields(view.from_env, "tracing")}
        what={t.save}
        onSave={(tracing) => onSave({ tracing })}
      />
      <SecretRows secrets={secrets} names={TRACING_KEYS} />
    </div>
  );
}
