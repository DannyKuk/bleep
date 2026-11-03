"use client";

import { KeyRound, Loader2, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";

import { Panel } from "@/components/panel";
import { HostSettings, type KnownService } from "@/components/settings/hosts";
import { ModelSettings } from "@/components/settings/models";
import { PluginList } from "@/components/settings/plugins";
import { TelegramSettings } from "@/components/settings/telegram";
import { ThresholdsForm } from "@/components/settings/thresholds";
import { apiFetch } from "@/lib/api";
import type { ServiceView } from "@/lib/api-types";
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

/** One GET, read on mount; `set` replaces the data with what a save returned. */
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
  return { ...state, set: (data: T) => setState({ data }) };
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
      <p role="alert" className="flex items-center gap-2 text-sm text-health-down">
        <TriangleAlert aria-hidden className="size-4" />
        Could not load {what}: {loaded.error.message}
      </p>
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
 * hosts' connections and every secret stay in the hosts file and the environment.
 */
export function SettingsPage() {
  const settings = useLoaded<SettingsView>("/api/settings");
  const services = useLoaded<ServiceView[]>("/api/services");
  const plugins = useLoaded<PluginView[]>("/api/plugins");
  const telegram = useLoaded<TelegramStatus>("/api/telegram/status");
  const known = servicesByHost(services.data);

  async function saveSettings(update: SettingsUpdate) {
    settings.set(
      await apiFetch<SettingsView>("/api/settings", {
        method: "PUT",
        body: JSON.stringify(update),
      }),
    );
  }

  async function savePlugin(name: string, update: PluginUpdate) {
    const saved = await apiFetch<PluginView>(`/api/plugins/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify(update),
    });
    plugins.set((plugins.data ?? []).map((plugin) => (plugin.name === name ? saved : plugin)));
  }

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h1 className="text-lg font-semibold">Settings</h1>
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <KeyRound aria-hidden className="size-4 shrink-0" />
          Secrets stay in the environment and the hosts file; this page shows whether each one is
          set, never its value. Every change here takes effect at once.
        </p>
      </header>

      <Status loaded={settings} what="the settings">
        {(view) => (
          <>
            {view.hosts.map((host) => (
              <HostSettings
                key={host.id}
                host={host}
                overrides={view.overrides.hosts?.[host.id]}
                services={known.get(host.id) ?? []}
                save={(overrides) => saveSettings({ hosts: { [host.id]: overrides } })}
              />
            ))}
            <Panel title="Thresholds">
              <ThresholdsForm
                key={JSON.stringify(view.thresholds)}
                values={view.thresholds}
                overrides={view.overrides.thresholds ?? {}}
                onSave={(thresholds) => saveSettings({ thresholds })}
              />
            </Panel>
          </>
        )}
      </Status>

      <Panel title="Plugins">
        <Status loaded={plugins} what="the plugins">
          {(list) => <PluginList plugins={list} onSave={savePlugin} />}
        </Status>
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Telegram">
          <Status loaded={telegram} what="the Telegram status">
            {(status) => <TelegramSettings status={status} />}
          </Status>
        </Panel>
        <Panel title="Models">
          <Status loaded={settings} what="the models">
            {(view) => <ModelSettings models={view.models} apiKey={view.model_key} />}
          </Status>
        </Panel>
      </div>
    </div>
  );
}
