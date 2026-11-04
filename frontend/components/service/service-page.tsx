"use client";

import { ArrowLeft, Loader2, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { HealthBadge } from "@/components/overview/health-badge";
import { IncidentStatusBadge } from "@/components/incident/status-badge";
import { subjectText } from "@/components/incident/incident-list";
import { Panel } from "@/components/panel";
import { MemoryPanel } from "@/components/service/memory-panel";
import { ApiError, apiFetch } from "@/lib/api";
import type { ContainerView, Health, Incident, ServiceDetail, UrlStatus } from "@/lib/api-types";
import { formatDateTime, formatTime } from "@/lib/format";
import { cn } from "@/lib/utils";

const PAST_INCIDENTS = 20;

interface Loaded<T> {
  data?: T;
  error?: Error;
}

/** One GET, read once when `path` changes. */
function useOnce<T>(path: string): Loaded<T> {
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
  return state;
}

function Failed({ children }: { children: React.ReactNode }) {
  return (
    <p role="alert" className="flex items-center gap-2 text-sm text-down">
      <TriangleAlert aria-hidden className="size-4" />
      {children}
    </p>
  );
}

function Loading({ children }: { children: React.ReactNode }) {
  return (
    <p role="status" className="flex items-center gap-2 text-sm text-ink-2">
      <Loader2 aria-hidden className="size-4 animate-spin" />
      {children}
    </p>
  );
}

const NEVER_WITHOUT_YES = "I never change anything without your yes.";

/** The rules Bleep follows for the service, its own first, then its host's. */
export function RuleList({ rules, hostRules }: { rules: string[]; hostRules: string[] }) {
  const groups = [
    { label: "This service", items: rules },
    { label: "Its host", items: hostRules },
  ].filter((group) => group.items.length > 0);
  return (
    <div className="flex flex-col gap-3">
      {groups.length === 0 && (
        <p className="text-sm text-ink-2">
          No rules of its own. Add them under the service in{" "}
          <code className="font-mono">hosts.yaml</code>.
        </p>
      )}
      {groups.map((group) => (
        <div key={group.label} className="flex flex-col gap-1.5">
          <h3 className="text-xs font-bold text-ink-2">{group.label}</h3>
          <ul aria-label={`Rules: ${group.label}`} className="flex flex-col gap-1.5">
            {group.items.map((rule) => (
              <li key={rule} className="rounded-row bg-soft px-3 py-2 text-sm">
                {rule}
              </li>
            ))}
          </ul>
        </div>
      ))}
      <p className="text-sm font-bold">{NEVER_WITHOUT_YES}</p>
    </div>
  );
}

function containerTone(container: ContainerView): string {
  if (container.health === "unhealthy") return "text-down";
  if (container.state === "running") {
    return container.health === "starting" ? "text-warn" : "text-ok";
  }
  if (container.state === "restarting") return "text-warn";
  // Exited 0 or by SIGTERM: stopped on purpose or finished, not a crash.
  if (container.state === "exited" && (container.exit_code === 0 || container.exit_code === 143)) {
    return "text-stop";
  }
  return "text-down";
}

function ContainerRow({ container }: { container: ContainerView }) {
  const detail =
    container.health === "none" ? container.state : `${container.state}, ${container.health}`;
  return (
    <li
      title={`${container.image} (${container.id.slice(0, 12)})`}
      className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 rounded-row bg-soft px-3 py-2 text-sm"
    >
      <span className="font-mono break-all">{container.name}</span>
      <span className={cn("font-bold", containerTone(container))}>{detail}</span>
    </li>
  );
}

const URL_HEALTH: Record<UrlStatus["status"], { label: string; health: Health }> = {
  ok: { label: "OK", health: "ok" },
  failing: { label: "Failing", health: "down" },
  unknown: { label: "Not checked yet", health: "unknown" },
};

function UrlRow({ url }: { url: UrlStatus }) {
  const { label: statusLabel, health } = URL_HEALTH[url.status];
  const label =
    url.status === "unknown" && url.last_checked != null ? "No recent check" : statusLabel;
  const facts = [
    url.status_code,
    url.latency_ms != null ? `${url.latency_ms} ms` : null,
    url.last_checked != null ? `checked ${formatTime(url.last_checked)}` : null,
  ].filter((part) => part != null);
  return (
    <li className="flex flex-col gap-1.5 rounded-row bg-soft px-3 py-2 text-sm">
      <span className="font-mono break-all">{url.url}</span>
      <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <HealthBadge health={health} label={label} />
        {facts.length > 0 && <span className="text-xs text-ink-2">{facts.join(" · ")}</span>}
      </span>
    </li>
  );
}

function PastIncidents({ incidents }: { incidents: Incident[] }) {
  if (incidents.length === 0) {
    return (
      <p className="text-sm text-ink-2">
        No incidents yet. When this service breaks, it shows here.
      </p>
    );
  }
  return (
    <ul aria-label="Incidents" className="flex flex-col gap-1.5">
      {incidents.map((incident) => (
        <li key={incident.id}>
          <Link
            href={`/incidents/${incident.id}`}
            className="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1 rounded-row bg-soft px-3 py-2 hover:bg-line focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          >
            <span className="min-w-0 flex-1 basis-60 text-sm font-bold">{incident.title}</span>
            <IncidentStatusBadge status={incident.status} />
            <span className="font-mono text-xs text-ink-2">{subjectText(incident)}</span>
            <time dateTime={incident.opened_at} className="text-xs text-ink-2">
              {formatDateTime(incident.opened_at)}
            </time>
          </Link>
        </li>
      ))}
    </ul>
  );
}

const count = (n: number, noun: string) => `${n} ${noun}${n === 1 ? "" : "s"}`;

/** A service's page: its health, what Bleep knows, its checks and containers, rules, incidents. */
export function ServicePage({ id }: { id: string }) {
  const encoded = encodeURIComponent(id);
  const detail = useOnce<ServiceDetail>(`/api/services/${encoded}`);
  const incidents = useOnce<Incident[]>(
    `/api/incidents?service_id=${encoded}&limit=${PAST_INCIDENTS}`,
  );
  const service = detail.data?.service;
  const missing = detail.error instanceof ApiError && detail.error.status === 404;
  const name = service?.name ?? id.split(":").pop() ?? id;

  return (
    <div className="flex flex-col gap-4">
      <Link
        href="/"
        className="inline-flex min-h-11 items-center gap-1 self-start text-sm font-bold text-accent hover:underline"
      >
        <ArrowLeft aria-hidden className="size-4" />
        Overview
      </Link>

      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <h1 className="text-3xl font-extrabold md:text-4xl">{name}</h1>
          {service && <HealthBadge health={service.health} />}
        </div>
        {service ? (
          <p className="text-sm text-ink-2">
            {service.source} service on <span className="font-mono">{service.host_id}</span>
            {" · "}
            {count(service.containers.length, "container")}
          </p>
        ) : (
          <p className="font-mono text-xs text-ink-2">{id}</p>
        )}
        {missing && (
          <p className="text-sm text-ink-2">
            Bleep does not see this service on its host now. What it remembers is below.
          </p>
        )}
        {detail.error && !missing && (
          <Failed>Could not load the service: {detail.error.message}</Failed>
        )}
        {!detail.data && !detail.error && <Loading>Loading the service</Loading>}
      </header>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title={`What I know about ${name}`} className="lg:col-span-2">
          <MemoryPanel serviceId={id} />
        </Panel>
        {service && (
          <>
            <Panel title="Checked from outside">
              {service.urls.length > 0 ? (
                <ul className="flex flex-col gap-1.5" aria-label="URLs">
                  {service.urls.map((url) => (
                    <UrlRow key={url.url} url={url} />
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-ink-2">No URLs are checked.</p>
              )}
            </Panel>
            <Panel title="Containers">
              {service.containers.length > 0 ? (
                <ul className="flex flex-col gap-1.5" aria-label="Containers">
                  {service.containers.map((container) => (
                    <ContainerRow key={container.id} container={container} />
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-ink-2">No containers.</p>
              )}
            </Panel>
            <Panel title="Rules I follow" className="lg:col-span-2">
              <RuleList rules={detail.data!.rules} hostRules={detail.data!.host_rules} />
            </Panel>
          </>
        )}
        <Panel title="Past incidents" className="lg:col-span-2">
          {incidents.data ? (
            <PastIncidents incidents={incidents.data} />
          ) : incidents.error ? (
            <Failed>Could not load the incidents: {incidents.error.message}</Failed>
          ) : (
            <Loading>Loading the incidents</Loading>
          )}
        </Panel>
      </div>
    </div>
  );
}
