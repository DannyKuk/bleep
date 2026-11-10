"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ShortName } from "@/components/incident/short-name";
import { IncidentStatusBadge } from "@/components/incident/status-badge";
import { useTimeZone } from "@/components/preferences";
import { apiFetch } from "@/lib/api";
import type { Incident, IncidentListItem } from "@/lib/api-types";
import { formatDateTime } from "@/lib/format";
import { useLanguage, useT } from "@/lib/i18n";

const LINK =
  "font-bold text-accent hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none";

/**
 * Where the incident stands in its group of repeats: a repeat links the group's first incident;
 * the first lists its repeats, newest first. Nothing for an incident that happened once.
 */
export function Repeats({ incident, repeats }: { incident: Incident; repeats: number }) {
  const [list, setList] = useState<IncidentListItem[] | null>(null);
  const zone = useTimeZone();
  const language = useLanguage();
  const t = useT().repeats;
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
        {t.repeat(repeats)}{" "}
        <Link href={`/incidents/${incident.fold_of}`} className={LINK}>
          {t.first}
        </Link>
      </p>
    );
  }
  if (!first) return null;
  const last = list?.[0]?.opened_at;
  return (
    <section aria-label={t.label} className="flex flex-col gap-2">
      <p className="text-sm font-bold">
        {t.seen(repeats)}
        {last && (
          <span className="font-normal text-ink-2">
            {t.last(formatDateTime(last, zone, language))}
          </span>
        )}
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
                {repeat.short_name && <ShortName name={repeat.short_name} className="text-sm" />}
                <time dateTime={repeat.opened_at} className="text-xs text-ink-2">
                  {formatDateTime(repeat.opened_at, zone, language)}
                </time>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
