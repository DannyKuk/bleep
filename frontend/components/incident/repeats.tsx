"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { IncidentStatusBadge } from "@/components/incident/status-badge";
import { apiFetch } from "@/lib/api";
import type { Incident, IncidentListItem } from "@/lib/api-types";
import { formatDateTime } from "@/lib/format";

const LINK =
  "font-bold text-accent hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none";

/**
 * Where the incident stands in its group of repeats: a repeat links the group's first incident;
 * the first lists its repeats, newest first. Nothing for an incident that happened once.
 */
export function Repeats({ incident, repeats }: { incident: Incident; repeats: number }) {
  const [list, setList] = useState<IncidentListItem[] | null>(null);
  const first = incident.fold_of === null && repeats > 1;

  useEffect(() => {
    if (!first) return;
    const abort = new AbortController();
    apiFetch<IncidentListItem[]>(
      `/api/incidents?fold_of=${encodeURIComponent(incident.id)}&limit=50`,
      { signal: abort.signal },
    )
      .then(setList)
      .catch(() => setList([]));
    return () => abort.abort();
  }, [first, incident.id, repeats]);

  if (incident.fold_of !== null) {
    return (
      <p role="note" className="text-sm text-ink-2">
        A repeat: this happened {repeats} times in all.{" "}
        <Link href={`/incidents/${incident.fold_of}`} className={LINK}>
          See the first time
        </Link>
      </p>
    );
  }
  if (!first) return null;
  const last = list?.[0]?.opened_at;
  return (
    <section aria-label="Repeats" className="flex flex-col gap-2">
      <p className="text-sm font-bold">
        Seen {repeats} times
        {last && <span className="font-normal text-ink-2"> · last {formatDateTime(last)}</span>}
      </p>
      {list && list.length > 0 && (
        <ul className="flex flex-col gap-1.5">
          {list.map((repeat) => (
            <li key={repeat.id}>
              <Link
                href={`/incidents/${repeat.id}`}
                className="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1 rounded-row bg-soft px-3 py-2 hover:bg-line focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
              >
                <IncidentStatusBadge status={repeat.status} />
                <time dateTime={repeat.opened_at} className="text-xs text-ink-2">
                  {formatDateTime(repeat.opened_at)}
                </time>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
