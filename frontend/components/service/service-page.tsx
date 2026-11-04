"use client";

import { ArrowLeft, Loader2, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { IncidentListView } from "@/components/incident/incident-list";
import { HealthBadge } from "@/components/overview/health-badge";
import { ContainerChip, UrlItem } from "@/components/overview/service-row";
import { Panel } from "@/components/panel";
import { MemoryPanel } from "@/components/service/memory-panel";
import { Badge } from "@/components/ui/badge";
import { ApiError, apiFetch } from "@/lib/api";
import type { Incident, ServiceDetail } from "@/lib/api-types";

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
    <p role="alert" className="flex items-center gap-2 text-sm text-health-down">
      <TriangleAlert aria-hidden className="size-4" />
      {children}
    </p>
  );
}

function Loading({ children }: { children: React.ReactNode }) {
  return (
    <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 aria-hidden className="size-4 animate-spin" />
      {children}
    </p>
  );
}

/** The rules Bleep follows for the service, its own first, then its host's. */
export function RuleList({ rules, hostRules }: { rules: string[]; hostRules: string[] }) {
  if (rules.length + hostRules.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No rules. Add them under the service in <code className="font-mono">hosts.yaml</code>.
      </p>
    );
  }
  const groups = [
    { label: "This service", items: rules },
    { label: "Its host", items: hostRules },
  ].filter((group) => group.items.length > 0);
  return (
    <div className="flex flex-col gap-3">
      {groups.map((group) => (
        <div key={group.label} className="flex flex-col gap-1">
          <h3 className="text-xs text-muted-foreground">{group.label}</h3>
          <ul aria-label={`Rules: ${group.label}`} className="flex list-disc flex-col gap-1 pl-5">
            {group.items.map((rule) => (
              <li key={rule} className="text-sm">
                {rule}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

/** A service's page: its containers and URLs, its rules, what Bleep remembers, its incidents. */
export function ServicePage({ id }: { id: string }) {
  const encoded = encodeURIComponent(id);
  const detail = useOnce<ServiceDetail>(`/api/services/${encoded}`);
  const incidents = useOnce<Incident[]>(
    `/api/incidents?service_id=${encoded}&limit=${PAST_INCIDENTS}`,
  );
  const service = detail.data?.service;
  const missing = detail.error instanceof ApiError && detail.error.status === 404;

  return (
    <div className="flex flex-col gap-4">
      <Link
        href="/"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft aria-hidden className="size-4" />
        Overview
      </Link>

      <header className="flex flex-col gap-1.5">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <h1 className="text-lg font-semibold">{service?.name ?? id}</h1>
          {service && <Badge>{service.source}</Badge>}
          {service && <HealthBadge health={service.health} />}
        </div>
        <p className="font-mono text-xs text-muted-foreground">{id}</p>
        {missing && (
          <p className="text-sm text-muted-foreground">
            Bleep does not see this service on its host now. What it remembers is below.
          </p>
        )}
        {detail.error && !missing && (
          <Failed>Could not load the service: {detail.error.message}</Failed>
        )}
        {!detail.data && !detail.error && <Loading>Loading the service</Loading>}
      </header>

      {detail.data && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Panel title="Containers and URLs">
            <div className="flex flex-col gap-3">
              {detail.data.service.containers.length > 0 ? (
                <ul className="flex flex-wrap gap-1.5" aria-label="Containers">
                  {detail.data.service.containers.map((container) => (
                    <ContainerChip key={container.id} container={container} />
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted-foreground">No containers.</p>
              )}
              {detail.data.service.urls.length > 0 ? (
                <ul className="flex flex-col gap-1" aria-label="URLs">
                  {detail.data.service.urls.map((url) => (
                    <UrlItem key={url.url} url={url} />
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted-foreground">No URLs are checked.</p>
              )}
            </div>
          </Panel>
          <Panel title="Rules">
            <RuleList rules={detail.data.rules} hostRules={detail.data.host_rules} />
          </Panel>
        </div>
      )}

      <Panel title="Memory">
        <MemoryPanel serviceId={id} />
      </Panel>

      <section aria-labelledby="past-incidents" className="flex flex-col gap-2">
        <h2 id="past-incidents" className="text-sm font-semibold">
          Past incidents
        </h2>
        {incidents.data ? (
          <IncidentListView flat incidents={incidents.data} />
        ) : incidents.error ? (
          <Failed>Could not load the incidents: {incidents.error.message}</Failed>
        ) : (
          <Loading>Loading the incidents</Loading>
        )}
      </section>
    </div>
  );
}
