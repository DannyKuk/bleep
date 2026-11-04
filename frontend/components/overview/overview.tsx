"use client";

import { Loader2, ServerOff, TriangleAlert } from "lucide-react";
import { useCallback } from "react";

import { useOverall } from "@/components/overall-state";
import { OverviewHero } from "@/components/overview/hero";
import { HostTile } from "@/components/overview/host-tile";
import { heroSituation, reachLine, unsureIds } from "@/components/overview/situation";
import { usePoll } from "@/hooks/use-poll";
import { apiFetch } from "@/lib/api";
import type { HostView, Incident, IncidentDetail, ServiceView } from "@/lib/api-types";
import { overallMood } from "@/lib/mood";

const SETUP_DOCS = "https://github.com/DannyKuk/bleep/blob/main/docs/setup.md";

type Details = Readonly<Record<string, IncidentDetail>>;

function NoHosts() {
  return (
    <div className="flex flex-col items-center gap-2 rounded-card-sm border border-dashed border-line px-6 py-12 text-center md:rounded-card">
      <ServerOff aria-hidden className="size-6 text-ink-2" />
      <p className="text-[15px] font-bold">
        No hosts configured. Add one in{" "}
        <code className="font-mono font-normal">config/hosts.yaml</code>
      </p>
      <a
        href={SETUP_DOCS}
        className="inline-flex min-h-11 items-center text-[15px] text-accent underline underline-offset-2"
      >
        Setup guide
      </a>
    </div>
  );
}

function Hosts({ hosts, services }: { hosts: HostView[]; services: ServiceView[] }) {
  return (
    <section aria-labelledby="your-hosts" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline gap-x-3.5 gap-y-1.5">
        <h2 id="your-hosts" className="text-xl font-extrabold">
          Your hosts
        </h2>
        <p className="text-[15px] text-ink-2">{reachLine(hosts, services)}</p>
      </div>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,300px),1fr))] gap-4">
        {hosts.map((host) => (
          <HostTile
            key={host.id}
            host={host}
            services={services.filter((service) => service.host_id === host.id)}
          />
        ))}
      </div>
    </section>
  );
}

/** The overview from its data: the hero, then the hosts with how far a problem reaches. */
export function OverviewView({
  hosts,
  services,
  incidents,
  details = {},
  now,
}: {
  hosts: HostView[];
  services: ServiceView[];
  incidents: Incident[];
  /** Details of the incidents that wait for the human, by id; one missing shows its title. */
  details?: Details;
  /** The moment the incidents were read, for how long a failed run still needs the human. */
  now: number;
}) {
  const situation = heroSituation({ incidents, hosts, details, now });
  const mood =
    situation.kind === "no_hosts"
      ? "resting"
      : overallMood(incidents, hosts, unsureIds(details), now);
  return (
    <div className="flex flex-col gap-10">
      <OverviewHero situation={situation} mood={mood} hosts={hosts} services={services} />
      {hosts.length === 0 ? <NoHosts /> : <Hosts hosts={hosts} services={services} />}
    </div>
  );
}

const POLL_MS = 10_000;

const isWaiting = (incident: Incident) => incident.status === "awaiting_approval";

// The services and the waiting incidents' details come from one tick and are committed together.
// A detail that cannot be read is left out, so the hero falls back to the incident's title.
async function fetchOverview(signal: AbortSignal, waitingIds: readonly string[]) {
  const [services, ...read] = await Promise.all([
    apiFetch<ServiceView[]>("/api/services", { signal }),
    ...waitingIds.map((id) =>
      apiFetch<IncidentDetail>(`/api/incidents/${encodeURIComponent(id)}`, { signal }).catch(
        () => null,
      ),
    ),
  ]);
  const details: Record<string, IncidentDetail> = {};
  waitingIds.forEach((id, index) => {
    const detail = read[index] as IncidentDetail | null;
    if (detail) details[id] = detail;
  });
  return { services, details };
}

/**
 * The overview: incidents and hosts from the app's shared poll (`useOverall`), the services and
 * the details of incidents waiting for the human from its own 10 s poll.
 */
export function Overview() {
  const overall = useOverall();
  const waitingKey = (overall.incidents ?? [])
    .filter(isWaiting)
    .map((incident) => incident.id)
    .join("\n");
  // A new waiting incident changes the fetcher, which restarts the poll and reads it at once.
  const fetcher = useCallback(
    (signal: AbortSignal) => fetchOverview(signal, waitingKey ? waitingKey.split("\n") : []),
    [waitingKey],
  );
  const { data, error: servicesError } = usePoll(fetcher, POLL_MS);
  const error = overall.error ?? servicesError;

  if (!overall.incidents || !overall.hosts || overall.readAt === undefined || !data) {
    if (error) {
      return (
        <p role="alert" className="flex items-center gap-2 text-[15px] text-down">
          <TriangleAlert aria-hidden className="size-4" />
          Could not load the overview: {error.message}
        </p>
      );
    }
    return (
      <p role="status" className="flex items-center gap-2 text-[15px] text-ink-2">
        <Loader2 aria-hidden className="size-4 animate-spin motion-reduce:animate-none" />
        Loading hosts
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-4">
      {error && (
        <p role="alert" className="flex items-center gap-2 text-[13px] text-warn">
          <TriangleAlert aria-hidden className="size-3.5" />
          Refresh failed, showing the last data: {error.message}
        </p>
      )}
      <OverviewView
        hosts={overall.hosts}
        services={data.services}
        incidents={overall.incidents}
        details={data.details}
        now={overall.readAt}
      />
    </div>
  );
}
