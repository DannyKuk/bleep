"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { useOverall } from "@/components/overall-state";
import { basicsLine } from "@/components/overview/host-tile";
import { HealthBadge } from "@/components/overview/health-badge";
import { Panel } from "@/components/panel";
import { QuietButton } from "@/components/quiet/quiet-button";
import { QuietPill } from "@/components/quiet/quiet-pill";
import { SaveLockProvider } from "@/components/save-lock";
import { HostSettings, type KnownService } from "@/components/settings/hosts";
import { Alert, Status, useLoaded, useStaleSaving } from "@/components/settings/loaded";
import { usePoll } from "@/hooks/use-poll";
import { apiFetch } from "@/lib/api";
import type { HostView, QuietView, ServiceView } from "@/lib/api-types";
import { quietLabel, sinceWords } from "@/lib/quiet-api";
import { serviceHref } from "@/lib/routes";
import { putSettings } from "@/lib/settings-api";
import type { SettingsUpdate, SettingsView } from "@/lib/settings-types";
import { cn } from "@/lib/utils";

const POLL_MS = 10_000;
const LINK =
  "font-bold text-accent hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none";

const fetchServices = (signal: AbortSignal) => apiFetch<ServiceView[]>("/api/services", { signal });

const count = (n: number, noun: string) => `${n} ${noun}${n === 1 ? "" : "s"}`;

/** The line under a quiet host's title: since or until when Bleep is silent about it. */
function quietNote(quiet: QuietView): string {
  const now = new Date();
  if (quiet.mode === "leave_alone" || quiet.until === null) {
    return `Since ${sinceWords(new Date(quiet.since), now)} Bleep opens no incidents for it or its services.`;
  }
  const until = quietLabel(quiet, now).replace(/^Host snoozed until /, "");
  return `Bleep opens no incidents for it or its services until ${until}.`;
}

/** What the services Bleep sees on the host are called, by their key there (`compose:shop`). */
function knownServices(hostId: string, services: ServiceView[]): KnownService[] {
  return services.map((service) => ({
    key: service.id.slice(hostId.length + 1),
    name: service.name,
  }));
}

function ServiceRow({ service }: { service: ServiceView }) {
  return (
    <li>
      <Link
        href={serviceHref(service.id)}
        className="flex min-h-11 flex-wrap items-center justify-between gap-x-3 gap-y-1 rounded-row bg-soft px-3 py-2 hover:bg-line focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
      >
        <span className="text-sm font-bold">{service.name}</span>
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

function Header({
  host,
  services,
  onChanged,
}: {
  host: HostView;
  services: number;
  onChanged: () => void;
}) {
  const basics = basicsLine(host);
  return (
    <header className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h1 className="text-3xl font-extrabold md:text-4xl">{host.name}</h1>
        {!host.quiet && (
          <HealthBadge health={host.health} label={host.reachable ? undefined : "Unreachable"} />
        )}
        <QuietButton
          target={{ scope: "host", id: host.id, name: host.name }}
          quiet={host.quiet}
          onChanged={onChanged}
        />
      </div>
      {host.quiet && (
        <p role="note" className="text-sm text-ink-2">
          {quietNote(host.quiet)}
        </p>
      )}
      <p className="text-sm text-ink-2">
        {host.kind === "ssh" ? "SSH" : "Docker socket"} host{" "}
        <span className="font-mono">{host.id}</span>
        {" · "}
        {count(services, "service")}
      </p>
      {basics && <p className="font-mono text-xs text-ink-2">{basics}</p>}
      {!host.reachable && host.error && (
        <p className={cn("font-mono text-xs break-words", host.quiet ? "text-ink-2" : "text-down")}>
          {host.error}
        </p>
      )}
    </header>
  );
}

/** A host's page: its state, its services, and its settings (connection, rules, each service's). */
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
          <li aria-current="page" className="font-bold text-ink">
            {name}
          </li>
        </ol>
      </nav>

      {missing ? (
        <div className="flex flex-col gap-2">
          <h1 className="text-3xl font-extrabold md:text-4xl">{id}</h1>
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
            <Header host={host} services={onHost.length} onChanged={refresh} />
          ) : (
            <h1 className="text-3xl font-extrabold md:text-4xl">{name}</h1>
          )}
          {notice && <Alert>{notice}</Alert>}

          <Panel title="Services">
            <Status loaded={services} what="the services">
              {() =>
                onHost.length > 0 ? (
                  <ul aria-label={`Services on ${name}`} className="flex flex-col gap-1.5">
                    {onHost.map((service) => (
                      <ServiceRow key={service.id} service={service} />
                    ))}
                  </ul>
                ) : (
                  <p className="text-sm text-ink-2">Bleep sees no services on this host yet.</p>
                )
              }
            </Status>
          </Panel>

          <Status loaded={settings} what="the host's settings">
            {(view) =>
              hostSettings && (
                <HostSettings
                  host={hostSettings}
                  overrides={view.overrides.hosts?.[id]}
                  services={knownServices(id, onHost)}
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
