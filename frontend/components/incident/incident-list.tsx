"use client";

import { Inbox, Loader2, TriangleAlert } from "lucide-react";
import Link from "next/link";

import { IncidentStatusBadge } from "@/components/incident/status-badge";
import { usePoll } from "@/hooks/use-poll";
import { apiFetch } from "@/lib/api";
import type { Incident } from "@/lib/api-types";
import { formatDateTime } from "@/lib/format";

const LIMIT = 50;
const POLL_MS = 10_000;

export function subjectText({ subject }: Incident): string {
  return subject.kind === "host"
    ? `host ${subject.host_id}`
    : (subject.service_id ?? subject.host_id);
}

export function IncidentListView({ incidents }: { incidents: Incident[] }) {
  if (incidents.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border px-6 py-12 text-center">
        <Inbox aria-hidden className="size-6 text-muted-foreground" />
        <p className="text-sm font-medium">
          No incidents yet. When a service breaks, it shows here.
        </p>
      </div>
    );
  }
  return (
    <ul
      aria-label="Incidents"
      className="divide-y divide-border rounded-lg border border-border bg-card"
    >
      {incidents.map((incident) => (
        <li key={incident.id}>
          <Link
            href={`/incidents/${incident.id}`}
            className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 hover:bg-accent/50 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          >
            <span className="min-w-0 flex-1 basis-60 text-sm font-medium">{incident.title}</span>
            <IncidentStatusBadge status={incident.status} />
            <span className="font-mono text-xs text-muted-foreground">{subjectText(incident)}</span>
            <time dateTime={incident.opened_at} className="text-xs text-muted-foreground">
              {formatDateTime(incident.opened_at)}
            </time>
          </Link>
        </li>
      ))}
    </ul>
  );
}

const fetchIncidents = (signal: AbortSignal) =>
  apiFetch<Incident[]>(`/api/incidents?limit=${LIMIT}`, { signal });

/** The newest incidents first, refreshed every 10 s. */
export function IncidentList() {
  const { data, error } = usePoll(fetchIncidents, POLL_MS);
  if (!data) {
    return error ? (
      <p role="alert" className="flex items-center gap-2 text-sm text-health-down">
        <TriangleAlert aria-hidden className="size-4" />
        Could not load incidents: {error.message}
      </p>
    ) : (
      <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 aria-hidden className="size-4 animate-spin" />
        Loading incidents
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-4">
      {error && (
        <p role="alert" className="flex items-center gap-2 text-xs text-health-degraded">
          <TriangleAlert aria-hidden className="size-3.5" />
          Refresh failed, showing the last data: {error.message}
        </p>
      )}
      <IncidentListView incidents={data} />
    </div>
  );
}
