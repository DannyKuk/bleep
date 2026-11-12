"use client";

import { Loader2, Lock } from "lucide-react";
import { useState } from "react";

import { HealthBadge } from "@/components/overview/health-badge";
import { CheckStrip } from "@/components/service/check-strip";
import { useLoaded, useStaleSaving } from "@/components/settings/loaded";
import { ListEditor } from "@/components/settings/rules";
import { SaveError, useSaving } from "@/components/settings/save-error";
import { UrlEditor } from "@/components/settings/urls";
import { useTimeZone } from "@/components/preferences";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";
import type { Health, UrlCheckPoint, UrlHistory, UrlStatus } from "@/lib/api-types";
import { formatTime } from "@/lib/format";
import { type Messages, useLanguage, useT } from "@/lib/i18n";
import { putSettings } from "@/lib/settings-api";
import type {
  HostOverrides,
  HostSettingsView,
  ServiceOverrides,
  ServiceSettingsView,
  SettingsView,
  UrlCheck,
} from "@/lib/settings-types";

const NOTHING: ServiceSettingsView = {
  urls: [],
  rules: [],
  protected_containers: [],
  hosts_file_protected_containers: [],
  exits: [],
};

/** Fields of a service's overrides to change; one set to undefined goes back to the hosts file's. */
export type ServiceChange = {
  [field in keyof ServiceOverrides]?: ServiceOverrides[field] | undefined;
};

/** The host's overrides with one service's fields changed; a service left with nothing is dropped. */
export function withService(
  host: HostOverrides | undefined,
  key: string,
  change: ServiceChange,
): HostOverrides {
  const service: ServiceOverrides = { ...host?.services?.[key], ...change };
  for (const field of Object.keys(service) as (keyof ServiceOverrides)[]) {
    if (service[field] === undefined) delete service[field];
  }
  const services = { ...host?.services };
  if (Object.keys(service).length > 0) services[key] = service;
  else delete services[key];
  return { ...host, services };
}

/**
 * The key a host's settings hold a service under: its own (`compose:shop`), else its name, which
 * the hosts file may use instead (`shop`) and discovery matches the same way.
 */
export function settingsKey(
  host: HostSettingsView,
  key: string,
  name?: string,
): string | undefined {
  if (key in host.services) return key;
  if (name !== undefined && name in host.services) return name;
  return undefined;
}

/** The name in a service's id (`shop` in `m1/compose:shop` or `m1/shop`), until it is read. */
export const nameIn = (id: string) =>
  id
    .slice(id.indexOf("/") + 1)
    .split(":")
    .pop() || id;

/** A service's settings as the page edits them. */
export interface ServiceSettings {
  hostId: string;
  /** In force: the hosts file's, with the dashboard's over it. */
  view: ServiceSettingsView;
  /** The dashboard's alone. */
  overrides: ServiceOverrides | undefined;
  hostRules: string[];
  save: (change: ServiceChange) => Promise<void>;
}

/**
 * The service's settings from `GET /api/settings`, saved through `PUT /api/settings` with the
 * version check and the stale notice. `seen` is the service as Bleep sees it now; one it does not
 * see has settings here only when the host's settings hold some for it, by its key or the name in
 * its id.
 */
export function useServiceSettings(id: string, seen: { name: string } | undefined) {
  const loaded = useLoaded<SettingsView>("/api/settings");
  const { notice, saving } = useStaleSaving();
  const all = loaded.data;
  const host = all?.hosts.find((candidate) => id.startsWith(`${candidate.id}/`));
  let settings: ServiceSettings | undefined;
  if (all && host) {
    const own = id.slice(host.id.length + 1);
    const key = settingsKey(host, own, seen?.name ?? nameIn(id)) ?? (seen ? own : undefined);
    const hostOverrides = all.overrides.hosts?.[host.id];
    if (key !== undefined) {
      settings = {
        hostId: host.id,
        view: host.services[key] ?? NOTHING,
        overrides: hostOverrides?.services?.[key],
        hostRules: host.rules,
        save: (change) =>
          saving(
            async () => {
              const update = { hosts: { [host.id]: withService(hostOverrides, key, change) } };
              loaded.set(await putSettings<SettingsView>(update, all.version));
            },
            async () => loaded.set(await apiFetch<SettingsView>("/api/settings")),
          ),
      };
    }
  }
  return { settings, error: loaded.error, notice };
}

function EditButton({ children, onClick }: { children: React.ReactNode; onClick: () => void }) {
  return (
    <div>
      <Button type="button" size="sm" variant="outline" onClick={onClick}>
        {children}
      </Button>
    </div>
  );
}

const URL_HEALTH: Record<UrlStatus["status"], Health> = {
  ok: "ok",
  failing: "down",
  unknown: "unknown",
};

function urlWords(status: UrlStatus, t: Messages): string {
  if (status.status === "ok") return t.health.states.ok;
  if (status.status === "failing") return t.health.failing;
  return status.last_checked != null ? t.health.noRecentCheck : t.health.notCheckedYet;
}

const notChecked = (url: string): UrlStatus => ({
  url,
  status: "unknown",
  last_checked: null,
  status_code: null,
  latency_ms: null,
});

function UrlRow({ url, checks }: { url: UrlStatus; checks: UrlCheckPoint[] }) {
  const zone = useTimeZone();
  const language = useLanguage();
  const t = useT();
  const health = URL_HEALTH[url.status];
  const label = urlWords(url, t);
  const facts = [
    url.status_code,
    url.latency_ms != null ? `${url.latency_ms} ms` : null,
    url.last_checked != null
      ? t.service.checkedAt(formatTime(url.last_checked, zone, language))
      : null,
  ].filter((part) => part != null);
  return (
    <li className="flex flex-col gap-1.5 rounded-row bg-soft px-3 py-2 text-sm">
      <span className="font-mono break-all">{url.url}</span>
      <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <HealthBadge health={health} label={label} />
        {facts.length > 0 && <span className="text-xs text-ink-2">{facts.join(" · ")}</span>}
      </span>
      <CheckStrip url={url.url} checks={checks} />
    </li>
  );
}

const sameUrl = (url: string) => url.trim().replace(/\/+$/, "").toLowerCase();

/** The suggested checks the service does not have yet, compared by their URLs. */
export function notYetChecked(suggested: unknown, urls: UrlCheck[]): UrlCheck[] {
  if (!Array.isArray(suggested)) return [];
  const had = new Set(urls.map((check) => sameUrl(check.url)));
  return (suggested as UrlCheck[]).filter((check) => !had.has(sameUrl(check.url)));
}

/**
 * The checks Coolify suggests from the service's domains (#348), added in one click to the URLs
 * in force and saved like any edit of them. Nothing is checked before.
 */
function SuggestedChecks({
  name,
  offered,
  settings,
}: {
  name: string;
  offered: UrlCheck[];
  settings: ServiceSettings;
}) {
  const t = useT();
  const { saving, busy, problems, run } = useSaving(t.settings.urls.labels);
  return (
    <div
      role="group"
      aria-label={t.service.suggestedFor(name)}
      className="flex flex-col gap-2 rounded-row border border-dashed border-line px-3 py-2 text-sm"
    >
      <p className="font-bold">{t.service.suggestedTitle}</p>
      <ul className="flex flex-col gap-1">
        {offered.map((check) => (
          <li key={check.url} className="font-mono break-all">
            {check.url}
          </li>
        ))}
      </ul>
      <p className="text-xs text-ink-2">{t.service.suggestedNote}</p>
      <div>
        <Button
          wrap
          type="button"
          size="sm"
          disabled={busy}
          onClick={() =>
            void run(() => settings.save({ urls: [...settings.view.urls, ...offered] }))
          }
        >
          {saving && <Loader2 aria-hidden className="animate-spin" />}
          {t.service.addSuggested(offered.length)}
        </Button>
      </div>
      <SaveError problems={problems} />
    </div>
  );
}

/**
 * The URLs Bleep checks for the service, each with its last check and the strip of its recent
 * ones. With settings, the list is the one in force (a URL just added shows as not checked yet)
 * and can be edited.
 */
export function CheckedUrls({
  name,
  statuses,
  history = [],
  settings,
  suggested,
}: {
  name: string;
  statuses: UrlStatus[];
  history?: UrlHistory[];
  settings?: ServiceSettings;
  /** The checks a plugin suggests for the service; offered only where the settings can be saved. */
  suggested?: UrlCheck[];
}) {
  const [editing, setEditing] = useState(false);
  const t = useT().service;
  if (settings && editing) {
    return (
      <UrlEditor
        key={JSON.stringify(settings.view.urls)}
        service={name}
        urls={settings.view.urls}
        overridden={settings.overrides?.urls !== undefined}
        onSave={(urls) => settings.save({ urls })}
        onReset={() => settings.save({ urls: undefined })}
        onClose={() => setEditing(false)}
      />
    );
  }
  const offered = settings ? notYetChecked(suggested, settings.view.urls) : [];
  const shown = settings
    ? settings.view.urls.map(
        (check) => statuses.find((status) => status.url === check.url) ?? notChecked(check.url),
      )
    : statuses;
  return (
    <div className="flex flex-col gap-3">
      {shown.length > 0 ? (
        <ul className="flex flex-col gap-1.5" aria-label="URLs">
          {shown.map((url) => (
            <UrlRow
              key={url.url}
              url={url}
              checks={history.find((recent) => recent.url === url.url)?.checks ?? []}
            />
          ))}
        </ul>
      ) : (
        <p className="text-sm text-ink-2">{t.noUrls}</p>
      )}
      {settings && <EditButton onClick={() => setEditing(true)}>{t.editUrls}</EditButton>}
      {settings && offered.length > 0 && (
        <SuggestedChecks name={name} offered={offered} settings={settings} />
      )}
    </div>
  );
}

/** The rules Bleep follows for the service, its own first, then its host's. */
export function RuleList({ rules, hostRules }: { rules: string[]; hostRules: string[] }) {
  const t = useT().service;
  const groups = [
    { label: t.thisService, items: rules },
    { label: t.itsHost, items: hostRules },
  ].filter((group) => group.items.length > 0);
  return (
    <div className="flex flex-col gap-3">
      {groups.length === 0 && <p className="text-sm text-ink-2">{t.noRules}</p>}
      {groups.map((group) => (
        <div key={group.label} className="flex flex-col gap-1.5">
          <h3 className="text-xs font-bold text-ink-2">{group.label}</h3>
          <ul aria-label={t.rulesGroup(group.label)} className="flex flex-col gap-1.5">
            {group.items.map((rule) => (
              <li key={rule} className="rounded-row bg-soft px-3 py-2 text-sm">
                {rule}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

/**
 * The rules Bleep follows, from the settings when it has them (then the service's own can be
 * edited here; the host's are edited on the host's page), else as the service's read gave them.
 */
export function FollowedRules({
  name,
  rules,
  hostRules,
  settings,
}: {
  name: string;
  rules: string[];
  hostRules: string[];
  settings?: ServiceSettings;
}) {
  const [editing, setEditing] = useState(false);
  const t = useT().service;
  return (
    <div className="flex flex-col gap-3">
      {settings && editing ? (
        <ListEditor
          key={JSON.stringify(settings.view.rules)}
          label={t.rulesFor(name)}
          items={settings.view.rules}
          addLabel={t.addRule}
          emptyText={t.noOwnRules}
          labels={{ rules: t.ruleWord }}
          overridden={settings.overrides?.rules !== undefined}
          onSave={(next) => settings.save({ rules: next })}
          onReset={() => settings.save({ rules: undefined })}
          onClose={() => setEditing(false)}
        />
      ) : (
        <>
          <RuleList
            rules={settings?.view.rules ?? rules}
            hostRules={settings?.hostRules ?? hostRules}
          />
          {settings && <EditButton onClick={() => setEditing(true)}>{t.editRules}</EditButton>}
        </>
      )}
      <p className="text-sm font-bold">{t.neverWithoutYes}</p>
    </div>
  );
}

function ContainerBadges({
  label,
  containers,
  fixed = false,
}: {
  label: string;
  containers: string[];
  fixed?: boolean;
}) {
  const t = useT().service;
  return (
    <ul aria-label={label} className="flex flex-wrap gap-1.5">
      {containers.map((container) => (
        <li key={container}>
          <Badge title={fixed ? t.fileProtection : undefined}>
            {fixed && <Lock aria-hidden className="mr-1 size-3" />}
            <span className="font-mono">{container}</span>
            {fixed && <span className="sr-only">{t.fromFile}</span>}
          </Badge>
        </li>
      ))}
    </ul>
  );
}

/**
 * The containers Bleep never acts on. The hosts file's are locked here; only the ones added on
 * the dashboard can be removed.
 */
export function ProtectedContainers({
  name,
  settings,
}: {
  name: string;
  settings: ServiceSettings;
}) {
  const [editing, setEditing] = useState(false);
  const t = useT().service;
  const fixed = settings.view.hosts_file_protected_containers;
  const added = settings.overrides?.protected_containers ?? [];
  return (
    <div className="flex flex-col gap-1.5">
      <h3 className="text-xs font-bold text-ink-2">{t.protected}</h3>
      <p className="text-xs text-ink-2">{t.neverActOn}</p>
      {fixed.length > 0 && (
        <ContainerBadges label={t.protectedByFile(name)} containers={fixed} fixed />
      )}
      {editing ? (
        <ListEditor
          key={JSON.stringify(added)}
          label={t.protectedHere(name)}
          items={added}
          addLabel={t.addContainer}
          emptyText={fixed.length > 0 ? t.noneAddedHere : t.none}
          mono
          labels={{ protected_containers: t.containerWord }}
          onSave={(containers) =>
            settings.save({ protected_containers: containers.length ? containers : undefined })
          }
          onClose={() => setEditing(false)}
        />
      ) : (
        <>
          {added.length > 0 && <ContainerBadges label={t.protectedHere(name)} containers={added} />}
          {fixed.length === 0 && added.length === 0 && (
            <p className="text-sm text-ink-2">{t.none}</p>
          )}
          <EditButton onClick={() => setEditing(true)}>{t.editProtected}</EditButton>
        </>
      )}
    </div>
  );
}

/**
 * The jobs of the service that are meant to finish: their exit 0 is done, not down, and opens
 * nothing; a crash is still an incident. The list here replaces the hosts file's.
 */
export function ExpectedExits({ name, settings }: { name: string; settings: ServiceSettings }) {
  const [editing, setEditing] = useState(false);
  const t = useT().service;
  const jobs = settings.view.exits;
  return (
    <div className="flex flex-col gap-1.5">
      <h3 className="text-xs font-bold text-ink-2">{t.expectedToExit}</h3>
      <p className="text-xs text-ink-2">{t.exitsNote}</p>
      {editing ? (
        <ListEditor
          key={JSON.stringify(jobs)}
          label={t.expectedFor(name)}
          items={jobs}
          addLabel={t.addJob}
          emptyText={t.none}
          mono
          labels={{ exits: t.jobWord }}
          overridden={settings.overrides?.exits !== undefined}
          onSave={(next) => settings.save({ exits: next })}
          onReset={() => settings.save({ exits: undefined })}
          onClose={() => setEditing(false)}
        />
      ) : (
        <>
          {jobs.length > 0 ? (
            <ContainerBadges label={t.expectedFor(name)} containers={jobs} />
          ) : (
            <p className="text-sm text-ink-2">{t.none}</p>
          )}
          <EditButton onClick={() => setEditing(true)}>{t.editJobs}</EditButton>
        </>
      )}
    </div>
  );
}
