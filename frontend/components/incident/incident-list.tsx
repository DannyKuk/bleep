"use client";

import { Inbox, Loader2, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { IncidentStatusBadge } from "@/components/incident/status-badge";
import {
  FILTERS,
  GROUP_TITLE,
  type Filter,
  formatDuration,
  groupIncidents,
  groupOf,
  headline,
  matchesFilter,
  matchesSearch,
} from "@/components/incident/incident-groups";
import { Input } from "@/components/ui/input";
import { usePoll } from "@/hooks/use-poll";
import { apiFetch } from "@/lib/api";
import type { Incident } from "@/lib/api-types";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

const LIMIT = 50;
const POLL_MS = 10_000;

export function subjectText({ subject }: Incident): string {
  return subject.kind === "host"
    ? `host ${subject.host_id}`
    : (subject.service_id ?? subject.host_id);
}

function Row({ incident }: { incident: Incident }) {
  const waiting = groupOf(incident.status) === "needs_you";
  const duration = formatDuration(incident.opened_at, incident.closed_at);
  return (
    <li>
      <Link
        href={`/incidents/${incident.id}`}
        className={cn(
          "flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1 rounded-row bg-surface px-4 py-3 hover:bg-soft focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
          waiting ? "border-2 border-down" : "border border-line",
        )}
      >
        <IncidentStatusBadge status={incident.status} />
        <span className="min-w-0 flex-1 basis-56 text-[15px] font-bold">{incident.title}</span>
        <span className="font-mono text-xs text-ink-2">{subjectText(incident)}</span>
        <span className="text-xs text-ink-2">
          <time dateTime={incident.opened_at}>{formatDateTime(incident.opened_at)}</time>
          {duration && ` · ${duration}`}
        </span>
      </Link>
    </li>
  );
}

/** `flat` is the plain list a service's page embeds: no headline, filters or groups. */
export function IncidentListView({
  incidents,
  flat = false,
}: {
  incidents: Incident[];
  flat?: boolean;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");

  if (incidents.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 rounded-card border border-dashed border-line px-6 py-12 text-center">
        <Inbox aria-hidden className="size-6 text-ink-2" />
        <p className="text-sm font-medium">
          No incidents yet. When a service breaks, it shows here.
        </p>
      </div>
    );
  }

  if (flat) {
    return (
      <ul aria-label="Incidents" className="flex flex-col gap-2">
        {incidents.map((incident) => (
          <Row key={incident.id} incident={incident} />
        ))}
      </ul>
    );
  }

  const shown = incidents.filter(
    (i) => matchesFilter(i.status, filter) && matchesSearch(i, subjectText(i), query),
  );
  return (
    <div className="flex flex-col gap-5">
      <h2 className="text-xl font-extrabold">{headline(incidents)}</h2>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <Input
          type="search"
          aria-label="Search incidents"
          placeholder="Search title or subject"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="sm:max-w-xs"
        />
        <div role="group" aria-label="Filter incidents" className="flex flex-wrap gap-2">
          {FILTERS.map(({ id, label }) => (
            <button
              key={id}
              type="button"
              aria-pressed={filter === id}
              onClick={() => setFilter(id)}
              className={cn(
                "min-h-11 rounded-full border px-4 text-sm font-bold focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                filter === id
                  ? "border-ink bg-ink text-bg"
                  : "border-line bg-surface text-ink-2 hover:bg-soft",
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      {shown.length === 0 ? (
        <p role="status" className="text-sm text-ink-2">
          No incident matches.
        </p>
      ) : (
        groupIncidents(shown).map(({ group, incidents: rows }) => (
          <section key={group} aria-labelledby={`group-${group}`} className="flex flex-col gap-2">
            <h3 id={`group-${group}`} className="text-sm font-extrabold text-ink-2">
              {GROUP_TITLE[group]} ({rows.length})
            </h3>
            <ul aria-label={GROUP_TITLE[group]} className="flex flex-col gap-2">
              {rows.map((incident) => (
                <Row key={incident.id} incident={incident} />
              ))}
            </ul>
          </section>
        ))
      )}
    </div>
  );
}

const fetchIncidents = (signal: AbortSignal) =>
  apiFetch<Incident[]>(`/api/incidents?limit=${LIMIT}`, { signal });

/** The newest incidents first, refreshed every 10 s. */
export function IncidentList() {
  const { data, error } = usePoll(fetchIncidents, POLL_MS);
  if (!data) {
    return error ? (
      <p role="alert" className="flex items-center gap-2 text-sm text-down">
        <TriangleAlert aria-hidden className="size-4" />
        Could not load incidents: {error.message}
      </p>
    ) : (
      <p role="status" className="flex items-center gap-2 text-sm text-ink-2">
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
