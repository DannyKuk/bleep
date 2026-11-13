"use client";

import { ChevronRight, KeyRound } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Panel } from "@/components/panel";
import { SaveLockProvider } from "@/components/save-lock";
import { HostForm } from "@/components/settings/host-form";
import { LanguageForm } from "@/components/settings/language-form";
import { Alert, Status, useLoaded, useStaleSaving } from "@/components/settings/loaded";
import { EMBEDDING_KEYS, EmbeddingSettings } from "@/components/settings/embeddings";
import { MODEL_KEYS, ModelSettings } from "@/components/settings/models";
import { PluginList } from "@/components/settings/plugins";
import { RepoDocs } from "@/components/settings/repo-docs";
import { TelegramSettings } from "@/components/settings/telegram";
import { SecretFields } from "@/components/settings/secret-field";
import { ThresholdsForm } from "@/components/settings/thresholds";
import { TimeZoneForm } from "@/components/settings/time-zone-form";
import { TRACING_KEYS, TracingSettings } from "@/components/settings/tracing";
import { useSetTimeZone } from "@/components/preferences";
import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";
import { setLanguage, useT } from "@/lib/i18n";
import {
  deleteSecret,
  isAddressChanged,
  putSecret,
  type SecretActions,
  type SecretView,
} from "@/lib/secrets-api";
import { hostHref } from "@/lib/routes";
import type { RepoDocsState } from "@/lib/knowledge-api";
import { putPlugin, putSettings } from "@/lib/settings-api";
import type {
  GeneralOverrides,
  HostSettingsView,
  PluginUpdate,
  PluginView,
  SettingsUpdate,
  SettingsView,
  TelegramStatus,
} from "@/lib/settings-types";

/**
 * The `general` section with one form's `fields` replaced by `change`: a save replaces the
 * section whole, so the other forms' fields in it are sent again as they are. A field named in
 * `fields` and left out of `change` is cleared.
 */
export function withGeneral(
  current: GeneralOverrides | undefined,
  fields: (keyof GeneralOverrides)[],
  change: GeneralOverrides,
): GeneralOverrides {
  const kept = Object.entries(current ?? {}).filter(
    ([field]) => !fields.includes(field as keyof GeneralOverrides),
  );
  return { ...Object.fromEntries(kept), ...change };
}

/** The hosts as links to their pages, where each is configured, and the add-a-host form. */
function Hosts({
  hosts,
  onChanged,
}: {
  hosts: HostSettingsView[];
  onChanged: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const t = useT().settings;
  return (
    <Panel title={t.panels.hosts}>
      <div className="flex flex-col gap-3">
        <p className="text-sm text-muted-foreground">{t.hosts.note}</p>
        {hosts.length === 0 && <p className="text-sm text-ink-2">{t.hosts.none}</p>}
        {hosts.length > 0 && (
          <ul aria-label={t.hosts.label} className="flex flex-col gap-1.5">
            {hosts.map((host) => (
              <li key={host.id}>
                <Link
                  href={hostHref(host.id)}
                  className="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1 rounded-row bg-soft px-3 py-2 hover:bg-line focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                >
                  <span className="text-sm font-bold">{host.name}</span>
                  <span className="text-xs text-muted-foreground">
                    {host.connection.source === "dashboard"
                      ? t.hosts.fromDashboard
                      : t.hosts.fromFile}
                  </span>
                  <ChevronRight aria-hidden className="ml-auto size-4 text-muted-foreground" />
                </Link>
              </li>
            ))}
          </ul>
        )}
        {open ? (
          <HostForm onChanged={onChanged} onClose={() => setOpen(false)} />
        ) : (
          <div className="flex flex-col gap-1.5">
            <p className="text-xs text-muted-foreground">{t.hosts.sshOnly}</p>
            <div>
              <Button wrap type="button" size="sm" onClick={() => setOpen(true)}>
                {t.hosts.addSsh}
              </Button>
            </div>
          </div>
        )}
      </div>
    </Panel>
  );
}

/**
 * What is Bleep-wide: the hosts as links to their own pages, thresholds, plugins, secrets,
 * models, embeddings, tracing, the repo docs and Telegram. Secrets are set and cleared here or in the
 * environment, and shown only as set or missing.
 */
export function SettingsPage() {
  return (
    <SaveLockProvider>
      <Settings />
    </SaveLockProvider>
  );
}

function Settings() {
  const t = useT().settings;
  const settings = useLoaded<SettingsView>("/api/settings");
  const plugins = useLoaded<PluginView[]>("/api/plugins");
  const telegram = useLoaded<TelegramStatus>("/api/telegram/status");
  const secrets = useLoaded<SecretView[]>("/api/secrets");
  const docs = useLoaded<RepoDocsState>("/api/knowledge/repos");
  const { notice, setNotice, saving } = useStaleSaving();
  const setZone = useSetTimeZone();
  const zoneInForce = settings.data?.timezone;
  useEffect(() => {
    if (zoneInForce !== undefined) setZone(zoneInForce);
  }, [zoneInForce, setZone]);
  const languageInForce = settings.data?.language;
  useEffect(() => {
    if (languageInForce === "en" || languageInForce === "de") setLanguage(languageInForce);
  }, [languageInForce]);

  const saveSettings = (update: SettingsUpdate, version: string) =>
    saving(
      async () => settings.set(await putSettings<SettingsView>(update, version)),
      async () => settings.set(await apiFetch<SettingsView>("/api/settings")),
    );

  const readSettings = async () => {
    settings.set(await apiFetch<SettingsView>("/api/settings"));
  };

  const savePlugin = (name: string, update: PluginUpdate, version: string) =>
    saving(
      async () => {
        const saved = await putPlugin<PluginView>(name, update, version);
        plugins.set((list) => (list ?? []).map((p) => (p.name === name ? saved : p)));
      },
      async () => plugins.set(await apiFetch<PluginView[]>("/api/plugins")),
    );

  /** What a changed secret changes elsewhere: a plugin's state, Telegram's status, and whether a
   * restart is needed (a tracer's key, the embeddings' address). A failed read says so instead of
   * leaving a stale page unmarked. */
  async function readAgain() {
    try {
      const [list, status, current] = await Promise.all([
        apiFetch<PluginView[]>("/api/plugins"),
        apiFetch<TelegramStatus>("/api/telegram/status"),
        apiFetch<SettingsView>("/api/settings"),
      ]);
      plugins.set(list);
      telegram.set(status);
      settings.set(current);
      setNotice(null);
    } catch (failed) {
      setNotice(t.savedButReread(failed instanceof Error ? failed.message : String(failed)));
    }
  }

  /** The value goes straight to the API and is not kept: only the list it answers is. */
  const secretActions: SecretActions = {
    set: async (name, value, addressVersion) => {
      try {
        secrets.set(await putSecret(name, value, addressVersion));
      } catch (error) {
        if (addressVersion === undefined || !isAddressChanged(error)) throw error;
        try {
          secrets.set(await apiFetch<SecretView[]>("/api/secrets"));
        } catch {
          throw new Error(t.addressNotChecked);
        }
        throw new Error(t.addressChanged);
      }
      await readAgain();
    },
    clear: async (name) => {
      secrets.set(await deleteSecret(name));
      await readAgain();
    },
  };
  const views = secrets.data ?? [];
  const inPlugins = new Set((plugins.data ?? []).flatMap((p) => p.secrets.map((s) => s.env)));
  const sourceOf = (name: string) => views.find((v) => v.name === name)?.source;
  const forPanels = secrets.data ? { views, actions: secretActions } : undefined;
  const inPanels = new Set<string>([...MODEL_KEYS, ...EMBEDDING_KEYS, ...TRACING_KEYS]);

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h1 className="text-lg font-semibold">{t.title}</h1>
        <p className="flex items-start gap-2 text-sm text-muted-foreground">
          <KeyRound aria-hidden className="mt-0.5 size-4 shrink-0" />
          <span>{t.intro}</span>
        </p>
      </header>

      {notice && <Alert>{notice}</Alert>}

      <Status loaded={settings} what="settings">
        {(view) => (
          <>
            <Hosts hosts={view.hosts} onChanged={readSettings} />
            <Panel title={t.panels.timeZone}>
              <TimeZoneForm
                key={`${view.timezone}/${view.overrides.general?.timezone}`}
                timezone={view.timezone}
                chosen={view.overrides.general?.timezone}
                onSave={(zone) =>
                  saveSettings(
                    { general: withGeneral(view.overrides.general, ["timezone"], zone) },
                    view.version,
                  )
                }
              />
            </Panel>
            <Panel title={t.panels.language}>
              <LanguageForm
                key={view.language}
                language={view.language}
                onSave={(language) =>
                  saveSettings(
                    { general: withGeneral(view.overrides.general, ["language"], { language }) },
                    view.version,
                  )
                }
              />
            </Panel>
            <Panel title={t.panels.thresholds}>
              <ThresholdsForm
                key={JSON.stringify(view.thresholds)}
                values={view.thresholds}
                overrides={view.overrides.thresholds ?? {}}
                onSave={(thresholds) => saveSettings({ thresholds }, view.version)}
              />
            </Panel>
            <Panel title={t.panels.models}>
              <ModelSettings
                key={JSON.stringify([view.models, view.overrides.models, view.from_env])}
                view={view}
                onSave={(update) => saveSettings(update, view.version)}
                secrets={forPanels}
              />
            </Panel>
            <Panel title={t.panels.embeddings}>
              <EmbeddingSettings
                key={JSON.stringify([view.embeddings, view.overrides.embeddings, view.from_env])}
                view={view}
                onSave={(update) => saveSettings(update, view.version)}
                secrets={forPanels}
              />
            </Panel>
            <Panel title={t.panels.tracing}>
              <TracingSettings
                key={JSON.stringify([view.tracing, view.overrides.tracing, view.from_env])}
                view={view}
                onSave={(update) => saveSettings(update, view.version)}
                secrets={forPanels}
              />
            </Panel>
          </>
        )}
      </Status>

      <Panel title={t.panels.plugins}>
        <Status loaded={plugins} what="plugins">
          {(list) => (
            <PluginList
              plugins={list}
              onSave={savePlugin}
              secrets={secrets.data ? { views, actions: secretActions } : undefined}
            />
          )}
        </Status>
      </Panel>

      <Panel title={t.panels.secrets}>
        <Status loaded={secrets} what="secrets">
          {(list) => (
            <SecretFields
              views={list.filter((v) => !inPlugins.has(v.name) && !inPanels.has(v.name))}
              actions={secretActions}
            />
          )}
        </Status>
      </Panel>

      <Panel title={t.panels.docs}>
        <Status loaded={docs} what="docs">
          {(state) => <RepoDocs initial={state} />}
        </Status>
      </Panel>

      <Panel title={t.panels.telegram}>
        <Status loaded={telegram} what="telegram">
          {(status) => (
            <TelegramSettings status={status} tokenSource={sourceOf(status.token.env ?? "")} />
          )}
        </Status>
      </Panel>
    </div>
  );
}
