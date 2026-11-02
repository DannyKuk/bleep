"use client";

import { RotateCcw, TriangleAlert } from "lucide-react";

import { usePoll } from "@/hooks/use-poll";
import { apiFetch } from "@/lib/api";
import type { RecoveryEvent } from "@/lib/api-types";
import { formatDateTime } from "@/lib/format";

const LIMIT = 10;
const POLL_MS = 30_000;

export function RecoveriesView({ recoveries }: { recoveries: RecoveryEvent[] }) {
  if (recoveries.length === 0) {
    return (
      <p className="px-4 py-3 text-sm text-muted-foreground">
        No self-recoveries yet. A container that dies and comes back within its grace window shows
        here instead of opening an incident.
      </p>
    );
  }
  return (
    <ul className="divide-y divide-border">
      {recoveries.map((recovery) => (
        <li
          key={recovery.id ?? `${recovery.container}-${recovery.at}`}
          className="flex flex-col gap-1 px-4 py-2.5"
        >
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
            <RotateCcw aria-hidden className="size-3.5 shrink-0 text-health-ok" />
            <span className="font-mono">{recovery.container}</span>
            <span className="text-xs text-muted-foreground">on</span>
            <span className="text-xs text-muted-foreground">{recovery.host_id}</span>
            <time dateTime={recovery.at} className="ml-auto text-xs text-muted-foreground">
              {formatDateTime(recovery.at)}
            </time>
          </div>
          <p className="text-sm text-muted-foreground">{recovery.explanation}</p>
        </li>
      ))}
    </ul>
  );
}

const fetchRecoveries = (signal: AbortSignal) =>
  apiFetch<RecoveryEvent[]>(`/api/recoveries?limit=${LIMIT}`, { signal });

/** Containers that died and came back on their own, newest first. */
export function Recoveries() {
  const { data, error } = usePoll(fetchRecoveries, POLL_MS);
  return (
    <section aria-labelledby="recoveries" className="rounded-lg border border-border bg-card">
      <header className="px-4 py-3">
        <h2 id="recoveries" className="text-sm font-semibold">
          Recovered on their own
        </h2>
      </header>
      <div className="border-t border-border">
        {error && (
          <p
            role="alert"
            className="flex items-center gap-2 px-4 pt-3 text-xs text-health-degraded"
          >
            <TriangleAlert aria-hidden className="size-3.5" />
            Could not load recoveries: {error.message}
          </p>
        )}
        {data && <RecoveriesView recoveries={data} />}
      </div>
    </section>
  );
}
