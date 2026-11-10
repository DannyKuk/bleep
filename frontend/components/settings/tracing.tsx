"use client";

import {
  envFields,
  type Field,
  type Group,
  type PanelSecrets,
  RestartNote,
  SecretRows,
  SectionForm,
} from "@/components/settings/section-form";
import type { SettingsUpdate, SettingsView } from "@/lib/settings-types";

export const TRACING_KEYS = [
  "LANGFUSE_PUBLIC_KEY",
  "LANGFUSE_SECRET_KEY",
  "LANGSMITH_API_KEY",
] as const;

const ADDRESS_RULE =
  "From the dashboard it must be https and reach public addresses only. It changes only while the keys sent to it are cleared: clear them, set the address, enter them again.";

const GROUPS: Group[] = [
  {
    fields: [
      {
        name: "tracing",
        label: "Trace incident runs with",
        env: "TRACING",
        options: [
          { value: "off", label: "Nothing" },
          { value: "langfuse", label: "Langfuse" },
          { value: "langsmith", label: "LangSmith" },
        ],
        hint: "Its keys must be set first.",
      },
    ],
  },
  {
    title: "Langfuse",
    fields: [
      {
        name: "langfuse_base_url",
        label: "Base URL",
        env: "LANGFUSE_BASE_URL",
        hint: ADDRESS_RULE,
      },
      {
        name: "langfuse_ui_url",
        label: "UI URL",
        env: "LANGFUSE_UI_URL",
        hint: "Only the link a browser opens to an incident's trace.",
      },
      { name: "langfuse_project_id", label: "Project id", env: "LANGFUSE_PROJECT_ID" },
    ],
  },
  {
    title: "LangSmith",
    fields: [
      {
        name: "langsmith_endpoint",
        label: "Endpoint",
        env: "LANGSMITH_ENDPOINT",
        hint: `Empty: LangSmith's own cloud. ${ADDRESS_RULE}`,
      },
      { name: "langsmith_project", label: "Project", env: "LANGSMITH_PROJECT" },
    ],
  },
];

const FIELDS: Field[] = GROUPS.flatMap((group) => group.fields);

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
  const values = Object.fromEntries(
    FIELDS.map(({ name }) => [name, view.tracing[name as keyof typeof view.tracing] ?? ""]),
  );
  return (
    <div className="flex flex-col gap-4">
      {view.restart_needed.includes("tracing") && (
        <RestartNote>
          A restart is needed: a change to a running Langfuse tracer applies at the next start.
        </RestartNote>
      )}
      <SectionForm
        groups={GROUPS}
        values={values}
        overrides={{ ...view.overrides.tracing }}
        fromEnv={envFields(view.from_env, "tracing")}
        what="Save tracing"
        onSave={(tracing) => onSave({ tracing })}
      />
      <SecretRows secrets={secrets} names={TRACING_KEYS} />
    </div>
  );
}
