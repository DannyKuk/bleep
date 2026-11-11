"use client";

import { Loader2, RotateCcw, TriangleAlert } from "lucide-react";
import Link from "next/link";

import { useTimeZone } from "@/components/preferences";
import { usePoll } from "@/hooks/use-poll";
import { apiFetch } from "@/lib/api";
import type { RecoveryEvent } from "@/lib/api-types";
import { formatDateTime } from "@/lib/format";
import { useLanguage, useT } from "@/lib/i18n";
import { serviceHref } from "@/lib/routes";

const LIMIT = 10;
const POLL_MS = 30_000;

function Container({ recovery }: { recovery: RecoveryEvent }) {
  if (!recovery.service_id) return <span className="font-mono text-sm">{recovery.container}</span>;
  return (
    <Link
      href={serviceHref(recovery.service_id)}
      className="inline-flex min-h-11 items-center rounded-full font-mono text-sm underline-offset-2 hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none sm:min-h-0"
    >
      {recovery.container}
    </Link>
  );
}

export function RecoveriesView({ recoveries }: { recoveries: RecoveryEvent[] }) {
  const zone = useTimeZone();
  const language = useLanguage();
  const t = useT().recoveries;
  if (recoveries.length === 0) {
    return (
      <p className="rounded-row border border-dashed border-line px-4 py-3 text-[15px] text-ink-2">
        {t.empty}
      </p>
    );
  }
  return (
    <ul className="flex flex-col gap-2">
      {recoveries.map((recovery) => (
        <li
          key={recovery.id ?? `${recovery.container}-${recovery.at}`}
          className="flex items-start gap-3 rounded-row border border-line bg-surface px-4 py-3"
        >
          <span
            aria-hidden
            className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-full bg-ok-bg text-ok"
          >
            <RotateCcw className="size-3.5" />
          </span>
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
              <Container recovery={recovery} />
              <span className="text-[13px] text-ink-2">
                {t.on[0]}
                <span className="font-mono">{recovery.host_id}</span>
                {t.on[1]}
              </span>
              <time
                dateTime={recovery.at}
                className="ml-auto text-[13px] font-bold whitespace-nowrap text-ink-2"
              >
                {formatDateTime(recovery.at, zone, language)}
              </time>
            </div>
            <p className="text-[15px] wrap-anywhere text-ink-2">{recovery.explanation}</p>
          </div>
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
  const t = useT().recoveries;
  return (
    <section aria-labelledby="recoveries" className="flex flex-col gap-3">
      <h2 id="recoveries" className="text-xl font-extrabold">
        {t.title}
      </h2>
      {error && (
        <p role="alert" className="flex items-center gap-2 text-[13px] text-warn">
          <TriangleAlert aria-hidden className="size-3.5" />
          {t.loadFailed(error.message)}
        </p>
      )}
      {!data && !error && (
        <p role="status" className="flex items-center gap-2 text-[15px] text-ink-2">
          <Loader2 aria-hidden className="size-4 animate-spin motion-reduce:animate-none" />
          {t.loading}
        </p>
      )}
      {data && <RecoveriesView recoveries={data} />}
    </section>
  );
}
