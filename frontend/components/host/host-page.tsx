"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback } from "react";

import { ENDED } from "@/components/incident/proposal-card";
import { useOverall } from "@/components/overall-state";
import { basicsLine, errorTone, healthLabel } from "@/components/overview/host-tile";
import { HealthBadge } from "@/components/overview/health-badge";
import { Panel } from "@/components/panel";
import { QuietButton } from "@/components/quiet/quiet-button";
import { QuietPill } from "@/components/quiet/quiet-pill";
import { SaveLockProvider } from "@/components/save-lock";
import { HostSettings } from "@/components/settings/hosts";
import { Alert, Status, useLoaded, useStaleSaving } from "@/components/settings/loaded";
import { useTimeZone } from "@/components/time-zone";
import { type Poll, usePoll } from "@/hooks/use-poll";
import { apiFetch } from "@/lib/api";
import type { HostView, IncidentListItem, QuietView, ServiceView } from "@/lib/api-types";
import { formatDateTime, formatSince } from "@/lib/format";
import { isAsleep } from "@/lib/mood";
import { quietLabel, sinceWords } from "@/lib/quiet-api";
import { serviceHref } from "@/lib/routes";
import { putSettings } from "@/lib/settings-api";
import type { SettingsUpdate, SettingsView } from "@/lib/settings-types";
import { cn } from "@/lib/utils";
import type { Zone } from "@/lib/zone";

const POLL_MS = 10_000;
const LINK =
  "font-bold text-accent hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none";

const fetchServices = (signal: AbortSignal) => apiFetch<ServiceView[]>("/api/services", { signal });

const count = (n: number, noun: string) => `${n} ${noun}${n === 1 ? "" : "s"}`;

/** The line under a quiet host's title: since or until when Bleep is silent about it. */
function quietNote(quiet: QuietView, zone: Zone): string {
  const now = new Date();
  if (quiet.mode === "leave_alone" || quiet.until === null) {
    return `Since ${sinceWords(new Date(quiet.since), now, zone)} Bleep opens no incidents for it or its services.`;
  }
  const until = quietLabel(quiet, now, zone).replace(/^Host snoozed until /, "");
  return `Bleep opens no incidents for it or its services until ${until}.`;
}

/**
 * The services the host's settings hold that Bleep does not see now, by their key there. One
 * the hosts file names (`shop`) matches the service with that key or that name, as discovery does.
 */
function unseenKeys(hostId: string, configured: string[], seen: ServiceView[]): string[] {
  const known = new Set(
    seen.flatMap((service) => [service.id.slice(hostId.length + 1), service.name]),
  );
  return configured.filter((key) => !known.has(key)).sort();
}

function ServiceRow({ service }: { service: ServiceView }) {
  return (
    <li>
      <Link
        href={serviceHref(service.id)}
        className="flex min-h-11 flex-wrap items-center justify-between gap-x-3 gap-y-1 rounded-row bg-soft px-3 py-2 hover:bg-line focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
      >
        <span className="min-w-0 text-sm font-bold wrap-anywhere">{service.name}</span>
        <span className="flex items-center gap-2">
          {service.open_incident_id && (
            <span className="text-[13px] font-bold text-down">Incident open</span>
          )}
          {service.quiet ? (
            <QuietPill quiet={service.quiet} />
          ) : (
            <HealthBadge health={service.health} />
          )}
        </span>
      </Link>
    </li>
  );
}

/** A service with settings that Bleep does not see; `unseen` is unknown while the read failed. */
function ConfiguredRow({
  hostId,
  serviceKey,
  unseen,
}: {
  hostId: string;
  serviceKey: string;
  unseen: boolean;
}) {
  return (
    <li>
      <Link
        href={serviceHref(`${hostId}/${serviceKey}`)}
        className="flex min-h-11 flex-wrap items-center justify-between gap-x-3 gap-y-1 rounded-row bg-soft px-3 py-2 hover:bg-line focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
      >
        <span className="min-w-0 font-mono text-sm font-bold wrap-anywhere">{serviceKey}</span>
        {unseen && <span className="text-[13px] font-bold text-ink-2">Not seen now</span>}
      </Link>
    </li>
  );
}

/** The services on the host, each linking to its page, where its settings are edited. */
function ServiceList({
  hostId,
  name,
  services,
  configured,
}: {
  hostId: string;
  name: string;
  services: Poll<ServiceView[]>;
  configured: string[];
}) {
  const read = services.data !== undefined;
  const seen = (services.data ?? []).filter((service) => service.host_id === hostId);
  const others = unseenKeys(hostId, configured, seen);
  return (
    <div className="flex flex-col gap-2">
      {!read && (
        <Status loaded={services} what="the services">
          {() => null}
        </Status>
      )}
      {seen.length + others.length > 0 ? (
        <ul aria-label={`Services on ${name}`} className="flex flex-col gap-1.5">
          {seen.map((service) => (
            <ServiceRow key={service.id} service={service} />
          ))}
          {others.map((key) => (
            <ConfiguredRow key={key} hostId={hostId} serviceKey={key} unseen={read} />
          ))}
        </ul>
      ) : (
        read && <p className="text-sm text-ink-2">Bleep sees no services on this host yet.</p>
      )}
    </div>
  );
}

/**
 * The line under a host that is still unreachable after its own last unreachable incident ended:
 * Bleep holds the next one back for a failure it could not fix, so the page says it is still
 * there. It follows the watcher's rule: a host that answered after that incident opened is down
 * anew, and opens its own. Exported for its tests.
 */
export function StillFailing({
  host,
  last,
}: {
  host: HostView;
  last: IncidentListItem | undefined;
}) {
  const zone = useTimeZone();
  if (host.quiet || host.reachable || isAsleep(host)) return null;
  if (!last || last.kind !== "host_unreachable" || !ENDED.has(last.status)) return null;
  if (host.last_seen !== null && new Date(host.last_seen) > new Date(last.opened_at)) return null;
  const ended = last.closed_at ?? last.opened_at;
  return (
    <p role="note" aria-label="Still failing" className="text-sm text-ink-2">
      Still unreachable after its last incident ended{" "}
      <time dateTime={ended} title={formatDateTime(ended, zone)}>
        {formatSince(ended, undefined, zone)}
      </time>
      :{" "}
      <Link href={`/incidents/${last.id}`} className={LINK}>
        {last.title}
      </Link>
      . Bleep opens a new one after a wait that grows with each repeat, from an hour to a day.
    </p>
  );
}

function Header({
  host,
  services,
  last,
  onChanged,
}: {
  host: HostView;
  services: number;
  last: IncidentListItem | undefined;
  onChanged: () => void;
}) {
  const zone = useTimeZone();
  const basics = basicsLine(host, zone);
  return (
    <header className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h1 className="text-3xl font-extrabold wrap-anywhere md:text-4xl">{host.name}</h1>
        {!host.quiet && <HealthBadge health={host.health} label={healthLabel(host)} />}
        <QuietButton
          target={{ scope: "host", id: host.id, name: host.name }}
          quiet={host.quiet}
          onChanged={onChanged}
        />
      </div>
      {host.quiet && (
        <p role="note" className="text-sm text-ink-2">
          {quietNote(host.quiet, zone)}
        </p>
      )}
      <StillFailing host={host} last={last} />
      <p className="text-sm text-ink-2">
        {host.kind === "ssh" ? "SSH" : "Docker socket"} host{" "}
        <span className="font-mono">{host.id}</span>
        {" · "}
        {count(services, "service")}
      </p>
      {basics && <p className="font-mono text-xs text-ink-2">{basics}</p>}
      {!host.reachable && host.error && (
        <p className={cn("font-mono text-xs break-words", errorTone(host))}>{host.error}</p>
      )}
    </header>
  );
}

/** A host's page: its state, its services, and its settings (connection and rules). */
export function HostPage({ id }: { id: string }) {
  return (
    <SaveLockProvider>
      <Host id={id} />
    </SaveLockProvider>
  );
}

function Host({ id }: { id: string }) {
  const router = useRouter();
  const overall = useOverall();
  const services = usePoll(fetchServices, POLL_MS);
  const fetchLastIncident = useCallback(
    (signal: AbortSignal) =>
      apiFetch<IncidentListItem[]>(`/api/incidents?host_id=${encodeURIComponent(id)}&limit=1`, {
        signal,
      }),
    [id],
  );
  const lastIncident = usePoll(fetchLastIncident, POLL_MS);
  const settings = useLoaded<SettingsView>("/api/settings");
  const { notice, saving } = useStaleSaving();

  const host = overall.hosts?.find((candidate) => candidate.id === id);
  const hostSettings = settings.data?.hosts.find((candidate) => candidate.id === id);
  const onHost = (services.data ?? []).filter((service) => service.host_id === id);
  const missing =
    overall.hosts !== undefined &&
    host === undefined &&
    settings.data !== undefined &&
    !hostSettings;
  const name = host?.name ?? hostSettings?.name ?? id;

  const readSettings = async () => {
    settings.set(await apiFetch<SettingsView>("/api/settings"));
  };
  const saveSettings = (update: SettingsUpdate, version: string) =>
    saving(
      async () => settings.set(await putSettings<SettingsView>(update, version)),
      readSettings,
    );
  const refresh = () => {
    overall.refresh();
    services.refresh();
  };

  return (
    <div className="flex flex-col gap-4">
      <nav aria-label="Breadcrumb">
        <ol className="flex flex-wrap items-center gap-x-2 text-sm text-ink-2">
          <li>
            <Link href="/" className={cn("inline-flex min-h-11 items-center", LINK)}>
              Home
            </Link>
          </li>
          <li aria-hidden>&rsaquo;</li>
          <li aria-current="page" className="min-w-0 font-bold wrap-anywhere text-ink">
            {name}
          </li>
        </ol>
      </nav>

      {missing ? (
        <div className="flex flex-col gap-2">
          <h1 className="text-3xl font-extrabold wrap-anywhere md:text-4xl">{id}</h1>
          <p className="text-sm text-ink-2">
            Bleep does not know a host called <span className="font-mono">{id}</span>.{" "}
            <Link href="/" className={LINK}>
              Back to your hosts
            </Link>
          </p>
        </div>
      ) : (
        <>
          {host ? (
            <Header
              host={host}
              services={onHost.length}
              last={lastIncident.data?.[0]}
              onChanged={refresh}
            />
          ) : (
            <h1 className="text-3xl font-extrabold wrap-anywhere md:text-4xl">{name}</h1>
          )}
          {overall.hosts === undefined && (
            <Status loaded={{ error: overall.error }} what="the host">
              {() => null}
            </Status>
          )}
          {notice && <Alert>{notice}</Alert>}

          <Panel title="Services">
            <ServiceList
              hostId={id}
              name={name}
              services={services}
              configured={Object.keys(hostSettings?.services ?? {})}
            />
          </Panel>

          <Status loaded={settings} what="the host's settings">
            {(view) =>
              hostSettings && (
                <HostSettings
                  host={hostSettings}
                  overrides={view.overrides.hosts?.[id]}
                  save={(overrides) => saveSettings({ hosts: { [id]: overrides } }, view.version)}
                  onHostChanged={readSettings}
                  onRemoved={async () => {
                    overall.refresh();
                    router.push("/");
                  }}
                />
              )
            }
          </Status>
        </>
      )}
    </div>
  );
}
