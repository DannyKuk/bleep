"use client";

import { Loader2, Power, PowerOff } from "lucide-react";
import { useId, useState } from "react";

import { HealthBadge } from "@/components/overview/health-badge";
import { SaveError, useSaving } from "@/components/settings/save-error";
import { SecretBadge } from "@/components/settings/secret";
import { SecretFields } from "@/components/settings/secret-field";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { type Messages, useT } from "@/lib/i18n";
import type { SecretActions, SecretView } from "@/lib/secrets-api";
import type { JsonSchemaProperty, PluginUpdate, PluginView } from "@/lib/settings-types";

type Kind = "string" | "integer" | "number" | "boolean" | "list" | "other";

/** What input a settings field gets; an optional field's schema is `anyOf: [type, null]`. */
export function kindOf(property: JsonSchemaProperty): Kind {
  const typed = property.type ? property : property.anyOf?.find((p) => p.type && p.type !== "null");
  const type = typed?.type;
  if (type === "array") return typed?.items?.type === "string" ? "list" : "other";
  if (type === "string" || type === "integer" || type === "number" || type === "boolean") {
    return type;
  }
  return "other";
}

/** A `SecretStr` field: Bleep refuses such a plugin, and the form never gives one an input. */
export function isSecret(property: JsonSchemaProperty): boolean {
  return [property, ...(property.anyOf ?? [])].some(
    (p) => p.writeOnly === true || p.format === "password",
  );
}

function asText(value: unknown, kind: Kind): string {
  if (value === undefined || value === null) return "";
  if (kind === "list" && Array.isArray(value)) return value.join("\n");
  if (kind === "other") return JSON.stringify(value);
  return String(value);
}

/** The draft as the API takes it. An empty field is left out, so its default holds; a number
 * that does not parse goes as typed, for the API to refuse. */
function settingsFrom(
  draft: Record<string, string>,
  fields: [string, JsonSchemaProperty][],
  current: Record<string, unknown>,
): Record<string, unknown> {
  const settings: Record<string, unknown> = {};
  for (const [name, property] of fields) {
    const kind = kindOf(property);
    const text = draft[name] ?? "";
    if (kind === "other") {
      if (name in current) settings[name] = current[name];
    } else if (kind === "boolean") {
      if (text !== "") settings[name] = text === "true";
    } else if (kind === "list") {
      settings[name] = text
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
    } else if (text.trim() !== "") {
      const number = Number(text.trim());
      settings[name] = kind === "string" || !Number.isFinite(number) ? text.trim() : number;
    }
  }
  return settings;
}

function placeholderOf(property: JsonSchemaProperty): string | undefined {
  if (property.default !== undefined && property.default !== "") return String(property.default);
  return property.examples?.[0];
}

function Field({
  name,
  property,
  value,
  onChange,
}: {
  name: string;
  property: JsonSchemaProperty;
  value: string;
  onChange: (value: string) => void;
}) {
  const id = useId();
  const t = useT().settings.plugins;
  const kind = kindOf(property);
  const label = property.title ?? name;
  if (kind === "boolean") {
    return (
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={value === "true"}
          onChange={(changed) => onChange(String(changed.target.checked))}
        />
        {label}
      </label>
    );
  }
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs text-muted-foreground">
        {label}
        {kind === "list" && t.onePerLine}
      </label>
      {kind === "list" ? (
        <textarea
          id={id}
          value={value}
          rows={3}
          onChange={(changed) => onChange(changed.target.value)}
          className="w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        />
      ) : (
        <Input
          id={id}
          value={value}
          readOnly={kind === "other"}
          inputMode={kind === "integer" || kind === "number" ? "decimal" : undefined}
          maxLength={kind === "string" ? property.maxLength : undefined}
          placeholder={placeholderOf(property)}
          onChange={(changed) => onChange(changed.target.value)}
        />
      )}
      {property.description && (
        <p className="text-xs text-muted-foreground">{property.description}</p>
      )}
    </div>
  );
}

const missingSecrets = (plugin: PluginView) =>
  plugin.secrets.filter((s) => s.required && !s.set).map((s) => s.env);

type PluginMessages = Messages["settings"]["plugins"];

/** "On", "Off", or, for a plugin switched on that does not run, what it is waiting for. */
export function state(plugin: PluginView, t: PluginMessages): string {
  if (plugin.enabled) return t.on;
  if (!plugin.switched_on) return t.off;
  const missing = missingSecrets(plugin);
  return missing.length > 0 ? t.onWaiting(missing.join(", ")) : t.onNotRunning;
}

function waitingNote(plugin: PluginView, t: PluginMessages): string | null {
  if (plugin.enabled || !plugin.switched_on) return null;
  const missing = missingSecrets(plugin);
  return missing.length > 0 ? t.waiting(missing.join(", ")) : t.couldNotStart;
}

/**
 * One plugin: switch it on or off, change its settings (a form from its JSON schema), see its
 * health and whether each secret it needs is set. Its address and token are set and cleared in
 * `SecretFields` when `secrets` has them; the settings form never has an input for either.
 */
export function PluginSettings({
  plugin,
  onSave,
  secrets,
}: {
  plugin: PluginView;
  onSave: (update: PluginUpdate) => Promise<unknown>;
  secrets?: { views: SecretView[]; actions: SecretActions };
}) {
  const own = plugin.secrets.map((secret) => secret.env);
  const views = (secrets?.views ?? []).filter((view) => own.includes(view.name));
  const fields = Object.entries(plugin.settings_schema.properties ?? {}).filter(
    ([, property]) => !isSecret(property),
  );
  // A checkbox shows the default until the plugin has settings; a text field shows it as the
  // placeholder, so an untouched one is left out and its default holds.
  const initial = () =>
    Object.fromEntries(
      fields.map(([name, property]) => {
        const kind = kindOf(property);
        const value =
          kind === "boolean" ? (plugin.settings[name] ?? property.default) : plugin.settings[name];
        return [name, asText(value, kind)];
      }),
    );
  const [draft, setDraft] = useState<Record<string, string>>(initial);
  const on = plugin.enabled || plugin.switched_on;
  const { plugins: t, cancel } = useT().settings;
  const { saving, busy, problems, run } = useSaving();
  const settings = settingsFrom(draft, fields, plugin.settings);
  const dirty =
    JSON.stringify(settings) !== JSON.stringify(settingsFrom(initial(), fields, plugin.settings));

  return (
    <section aria-label={t.plugin(plugin.title)} className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-medium">{plugin.title}</h3>
        <Badge>{state(plugin, t)}</Badge>
        {plugin.health && (
          <HealthBadge
            health={plugin.health.ok ? "ok" : "down"}
            label={plugin.health.ok ? t.healthy : t.unhealthy}
          />
        )}
        <Button
          wrap
          type="button"
          size="sm"
          variant="outline"
          className="ml-auto"
          disabled={busy}
          onClick={() => void run(() => onSave({ enabled: !on }))}
        >
          {on ? <PowerOff aria-hidden /> : <Power aria-hidden />}
          {on ? t.switchOff : t.switchOn}
        </Button>
      </div>
      {waitingNote(plugin, t) && (
        <p className="text-xs text-muted-foreground">{waitingNote(plugin, t)}</p>
      )}
      {plugin.health?.detail && (
        <p className="text-xs text-muted-foreground">{plugin.health.detail}</p>
      )}
      {secrets && views.length > 0 ? (
        <SecretFields views={views} actions={secrets.actions} />
      ) : (
        plugin.secrets.length > 0 && (
          <ul aria-label={t.secretsFor(plugin.title)} className="flex flex-wrap gap-1.5">
            {plugin.secrets.map((secret) => (
              <li key={secret.env}>
                <SecretBadge
                  secret={{ env: secret.env, file: null, set: secret.set }}
                  optional={!secret.required}
                />
              </li>
            ))}
          </ul>
        )
      )}
      {fields.length > 0 && (
        <form
          className="flex flex-col gap-2"
          onSubmit={(submitted) => {
            submitted.preventDefault();
            void run(() => onSave({ settings }));
          }}
        >
          {fields.map(([name, property]) => (
            <Field
              key={name}
              name={name}
              property={property}
              value={draft[name] ?? ""}
              onChange={(value) => setDraft({ ...draft, [name]: value })}
            />
          ))}
          {dirty && (
            <div className="flex gap-2">
              <Button wrap type="submit" size="sm" disabled={busy}>
                {saving && <Loader2 aria-hidden className="animate-spin" />}
                {t.saveSettings}
              </Button>
              <Button
                wrap
                type="button"
                size="sm"
                variant="ghost"
                disabled={saving}
                onClick={() => setDraft(initial())}
              >
                {cancel}
              </Button>
            </div>
          )}
        </form>
      )}
      <SaveError problems={problems} />
    </section>
  );
}

/** Every plugin Bleep has; each is off until a person switches it on. */
export function PluginList({
  plugins,
  onSave,
  secrets,
}: {
  plugins: PluginView[];
  /** `version` is that of the plugin's view the update was built from. */
  onSave: (name: string, update: PluginUpdate, version: string) => Promise<unknown>;
  /** What `GET /api/secrets` lists, and how to set and clear one. */
  secrets?: { views: SecretView[]; actions: SecretActions };
}) {
  const t = useT().settings.plugins;
  if (plugins.length === 0) {
    return <p className="text-sm text-muted-foreground">{t.none}</p>;
  }
  return (
    <div className="flex flex-col divide-y divide-border [&>*]:py-3 [&>*:first-child]:pt-0 [&>*:last-child]:pb-0">
      {plugins.map((plugin) => (
        <PluginSettings
          key={`${plugin.name}:${JSON.stringify(plugin.settings)}`}
          plugin={plugin}
          onSave={(update) => onSave(plugin.name, update, plugin.version)}
          secrets={secrets}
        />
      ))}
    </div>
  );
}
