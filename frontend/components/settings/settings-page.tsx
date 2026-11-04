"use client";

import { KeyRound, Loader2, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";

import { Panel } from "@/components/panel";
import { SaveLockProvider } from "@/components/save-lock";
import { HostSettings, type KnownService } from "@/components/settings/hosts";
import { ModelSettings } from "@/components/settings/models";
import { PluginList } from "@/components/settings/plugins";
import { TelegramSettings } from "@/components/settings/telegram";
import { SecretFields } from "@/components/settings/secret-field";
import { ThresholdsForm } from "@/components/settings/thresholds";
import { apiFetch } from "@/lib/api";
import type { ServiceView } from "@/lib/api-types";
import {
  ADDRESS_CHANGED,
  deleteSecret,
  isAddressChanged,
  putSecret,
  type SecretActions,
  type SecretView,
} from "@/lib/secrets-api";
import {
  isStale,
  putPlugin,
  putSettings,
  rereadFailed,
  SETTINGS_CHANGED,
  SettingsChanged,
} from "@/lib/settings-api";
import type {
  PluginUpdate,
  PluginView,
  SettingsUpdate,
  SettingsView,
  TelegramStatus,
} from "@/lib/settings-types";

interface Loaded<T> {
  data?: T;
  error?: Error;
}

/** One GET, read on mount; `set` replaces the data with what a save returned, or updates it. */
function useLoaded<T>(path: string) {
  const [state, setState] = useState<Loaded<T>>({});
  useEffect(() => {
    const controller = new AbortController();
    apiFetch<T>(path, { signal: controller.signal })
      .then((data) => setState({ data }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({ error: error instanceof Error ? error : new Error(String(error)) });
      });
    return () => controller.abort();
  }, [path]);
  const set = (next: T | ((data: T | undefined) => T)) =>
    setState((now) => ({
      data: typeof next === "function" ? (next as (data: T | undefined) => T)(now.data) : next,
    }));
  return { ...state, set };
}

function Alert({ children }: { children: React.ReactNode }) {
  return (
    <p role="alert" className="flex items-center gap-2 text-sm text-health-down">
      <TriangleAlert aria-hidden className="size-4 shrink-0" />
      <span>{children}</span>
    </p>
  );
}

function Status<T>({
  loaded,
  what,
  children,
}: {
  loaded: Loaded<T>;
  what: string;
  children: (data: T) => React.ReactNode;
}) {
  if (loaded.data !== undefined) return children(loaded.data);
  if (loaded.error) {
    return (
      <Alert>
        Could not load {what}: {loaded.error.message}
      </Alert>
    );
  }
  return (
    <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 aria-hidden className="size-4 animate-spin" />
      Loading {what}
    </p>
  );
}

/** The services Bleep sees on each host, by their key there. */
function servicesByHost(services: ServiceView[] | undefined): Map<string, KnownService[]> {
  const byHost = new Map<string, KnownService[]>();
  for (const service of services ?? []) {
    const key = service.id.slice(service.host_id.length + 1);
    byHost.set(service.host_id, [
      ...(byHost.get(service.host_id) ?? []),
      { key, name: service.name },
    ]);
  }
  return byHost;
}

/**
 * Everything a person may change from the dashboard, and the state of what they may not: the
 * hosts' connections stay in the hosts file. Secrets are set and cleared here or in the
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
  const settings = useLoaded<SettingsView>("/api/settings");
  const services = useLoaded<ServiceView[]>("/api/services");
  const plugins = useLoaded<PluginView[]>("/api/plugins");
  const telegram = useLoaded<TelegramStatus>("/api/telegram/status");
  const secrets = useLoaded<SecretView[]>("/api/secrets");
  const [notice, setNotice] = useState<string | null>(null);
  const known = servicesByHost(services.data);

  /** Runs a save built from a view at some version. A stale one reads the data again and is
   * never retried, since its body would put back what changed elsewhere. */
  async function saving(put: () => Promise<void>, readAgain: () => Promise<void>) {
    try {
      await put();
      setNotice(null);
    } catch (error) {
      if (!isStale(error)) throw error;
      try {
        await readAgain();
        setNotice(SETTINGS_CHANGED);
      } catch (failed) {
        setNotice(rereadFailed(failed));
      }
      throw new SettingsChanged();
    }
  }

  const saveSettings = (update: SettingsUpdate, version: string) =>
    saving(
      async () => settings.set(await putSettings<SettingsView>(update, version)),
      async () => settings.set(await apiFetch<SettingsView>("/api/settings")),
    );

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
          // the message below tells the person to reload
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
          one is set, never its value. Hosts connect as the hosts file says. Every change here takes
          effect at once.
        </p>
      </header>

      {notice && <Alert>{notice}</Alert>}
      {services.error && (
        <Alert>
          Could not load the services Bleep sees: {services.error.message}. Only services with
          settings of their own are listed.
        </Alert>
      )}

      <Status loaded={settings} what="the settings">
        {(view) => (
          <>
            {view.hosts.map((host) => (
              <HostSettings
                key={host.id}
                host={host}
                overrides={view.overrides.hosts?.[host.id]}
                services={known.get(host.id) ?? []}
                save={(overrides) =>
                  saveSettings({ hosts: { [host.id]: overrides } }, view.version)
                }
              />
            ))}
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
