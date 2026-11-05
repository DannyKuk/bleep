"use client";

import { ChevronRight, KeyRound } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Panel } from "@/components/panel";
import { SaveLockProvider } from "@/components/save-lock";
import { HostForm, SSH_ONLY } from "@/components/settings/host-form";
import { Alert, Status, useLoaded, useStaleSaving } from "@/components/settings/loaded";
import { ModelSettings } from "@/components/settings/models";
import { PluginList } from "@/components/settings/plugins";
import { TelegramSettings } from "@/components/settings/telegram";
import { SecretFields } from "@/components/settings/secret-field";
import { ThresholdsForm } from "@/components/settings/thresholds";
import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";
import {
  ADDRESS_CHANGED,
  ADDRESS_NOT_CHECKED,
  deleteSecret,
  isAddressChanged,
  putSecret,
  type SecretActions,
  type SecretView,
} from "@/lib/secrets-api";
import { hostHref } from "@/lib/routes";
import { putPlugin, putSettings } from "@/lib/settings-api";
import type {
  HostSettingsView,
  PluginUpdate,
  PluginView,
  SettingsUpdate,
  SettingsView,
  TelegramStatus,
} from "@/lib/settings-types";

/** The hosts as links to their pages, where each is configured, and the add-a-host form. */
function Hosts({
  hosts,
  onChanged,
}: {
  hosts: HostSettingsView[];
  onChanged: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Panel title="Hosts">
      <div className="flex flex-col gap-3">
        <p className="text-sm text-muted-foreground">
          Each host&apos;s connection, rules and services are set on its own page.
        </p>
        {hosts.length === 0 && <p className="text-sm text-ink-2">No hosts yet.</p>}
        {hosts.length > 0 && (
          <ul aria-label="Hosts" className="flex flex-col gap-1.5">
            {hosts.map((host) => (
              <li key={host.id}>
                <Link
                  href={hostHref(host.id)}
                  className="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1 rounded-row bg-soft px-3 py-2 hover:bg-line focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                >
                  <span className="text-sm font-bold">{host.name}</span>
                  <span className="text-xs text-muted-foreground">
                    {host.connection.source === "dashboard"
                      ? "added on the dashboard"
                      : "from config/hosts.yaml"}
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
            <p className="text-xs text-muted-foreground">{SSH_ONLY}</p>
            <div>
              <Button type="button" size="sm" onClick={() => setOpen(true)}>
                Add an SSH host
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
 * Telegram and models. Secrets are set and cleared here or in the environment, and shown only as
 * set or missing.
 */
export function SettingsPage() {
  return (
    <SaveLockProvider>
      <Settings />
    </SaveLockProvider>
  );
}

function Settings() {
  const settings = useLoaded<SettingsView>("/api/settings");
  const plugins = useLoaded<PluginView[]>("/api/plugins");
  const telegram = useLoaded<TelegramStatus>("/api/telegram/status");
  const secrets = useLoaded<SecretView[]>("/api/secrets");
  const { notice, setNotice, saving } = useStaleSaving();

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

  /** What a changed secret changes elsewhere: a plugin's state, Telegram's status. A failed
   * read says so instead of leaving a stale page unmarked. */
  async function readAgain() {
    try {
      const [list, status] = await Promise.all([
        apiFetch<PluginView[]>("/api/plugins"),
        apiFetch<TelegramStatus>("/api/telegram/status"),
      ]);
      plugins.set(list);
      telegram.set(status);
      setNotice(null);
    } catch (failed) {
      setNotice(
        `Saved, but reading the plugins and Telegram again failed (${failed instanceof Error ? failed.message : String(failed)}); reload the page.`,
      );
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
          throw new Error(ADDRESS_NOT_CHECKED);
        }
        throw new Error(ADDRESS_CHANGED);
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

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h1 className="text-lg font-semibold">Settings</h1>
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <KeyRound aria-hidden className="size-4 shrink-0" />
          Keys, tokens and plugin addresses are set below or in .env; this page shows whether each
          one is set, never its value. Every change here takes effect at once.
        </p>
      </header>

      {notice && <Alert>{notice}</Alert>}

      <Status loaded={settings} what="the settings">
        {(view) => (
          <>
            <Hosts hosts={view.hosts} onChanged={readSettings} />
            <Panel title="Thresholds">
              <ThresholdsForm
                key={JSON.stringify(view.thresholds)}
                values={view.thresholds}
                overrides={view.overrides.thresholds ?? {}}
                onSave={(thresholds) => saveSettings({ thresholds }, view.version)}
              />
            </Panel>
          </>
        )}
      </Status>

      <Panel title="Plugins">
        <Status loaded={plugins} what="the plugins">
          {(list) => (
            <PluginList
              plugins={list}
              onSave={savePlugin}
              secrets={secrets.data ? { views, actions: secretActions } : undefined}
            />
          )}
        </Status>
      </Panel>

      <Panel title="Secrets">
        <Status loaded={secrets} what="the secrets">
          {(list) => (
            <SecretFields
              views={list.filter((v) => !inPlugins.has(v.name))}
              actions={secretActions}
            />
          )}
        </Status>
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Telegram">
          <Status loaded={telegram} what="the Telegram status">
            {(status) => (
              <TelegramSettings status={status} tokenSource={sourceOf(status.token.env ?? "")} />
            )}
          </Status>
        </Panel>
        <Panel title="Models">
          <Status loaded={settings} what="the models">
            {(view) => (
              <ModelSettings
                models={view.models}
                apiKey={view.model_key}
                keySource={sourceOf(view.model_key.env ?? "")}
              />
            )}
          </Status>
        </Panel>
      </div>
    </div>
  );
}
