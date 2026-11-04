"use client";

import { ExternalLink, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Failed, formatCount, formatUsd, Loading, useGet } from "@/components/dev/status";
import { Button } from "@/components/ui/button";
import type { CostView, Usage } from "@/lib/api-types";

const DAYS = 30;

/** Only an http(s) link is rendered as one. */
export const isWebLink = (url: string | null): url is string =>
  url !== null && /^https?:\/\//i.test(url);

/** The cost, with how many calls it leaves out because their price is unknown. */
export function costText({ usd, unpriced }: Usage): string {
  return unpriced > 0 ? `${formatUsd(usd)} + ${unpriced} unpriced` : formatUsd(usd);
}

const cell = "px-2 py-1.5 text-right tabular-nums whitespace-nowrap";
const head = "px-2 py-1.5 text-right font-medium text-muted-foreground whitespace-nowrap";

function UsageCells({ usage }: { usage: Usage }) {
  return (
    <>
      <td className={cell}>{formatCount(usage.calls)}</td>
      <td className={cell}>{formatCount(usage.tokens_in)}</td>
      <td className={cell}>{formatCount(usage.tokens_out)}</td>
      <td className={cell}>{costText(usage)}</td>
    </>
  );
}

function UsageHeads() {
  return (
    <>
      <th className={head}>Calls</th>
      <th className={head}>Tokens in</th>
      <th className={head}>Tokens out</th>
      <th className={head}>Cost</th>
    </>
  );
}

export function CostTables({ costs }: { costs: CostView }) {
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm">
        Today: {formatUsd(costs.spent_today_usd)} of the {formatUsd(costs.daily_cap_usd)} daily cap
      </p>
      <div className="overflow-x-auto">
        <table aria-label="Cost per day" className="w-full text-sm">
          <thead className="border-b border-border">
            <tr>
              <th className="px-2 py-1.5 text-left font-medium text-muted-foreground">Day</th>
              <UsageHeads />
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {costs.days.length === 0 && (
              <tr>
                <td colSpan={5} className="px-2 py-3 text-muted-foreground">
                  No model calls in the last {DAYS} days.
                </td>
              </tr>
            )}
            {costs.days.map((day) => (
              <tr key={day.day}>
                <td className="px-2 py-1.5 font-mono whitespace-nowrap">{day.day}</td>
                <UsageCells usage={day} />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="overflow-x-auto">
        <table aria-label="Cost per incident" className="w-full text-sm">
          <thead className="border-b border-border">
            <tr>
              <th className="px-2 py-1.5 text-left font-medium text-muted-foreground">Incident</th>
              <UsageHeads />
              <th className={head}>Trace</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {costs.incidents.length === 0 && (
              <tr>
                <td colSpan={6} className="px-2 py-3 text-muted-foreground">
                  No incident has called a model yet.
                </td>
              </tr>
            )}
            {costs.incidents.map((row) => (
              <tr key={row.incident_id}>
                <td className="max-w-64 min-w-40 px-2 py-1.5">
                  {row.title ? (
                    <Link
                      href={`/incidents/${row.incident_id}`}
                      className="underline underline-offset-2 hover:text-foreground"
                    >
                      {row.title}
                    </Link>
                  ) : (
                    <span className="font-mono text-xs">{row.incident_id}</span>
                  )}
                </td>
                <UsageCells usage={row} />
                <td className={cell}>
                  {isWebLink(row.trace_url) ? (
                    <a
                      href={row.trace_url}
                      target="_blank"
                      rel="noreferrer"
                      aria-label={`Trace of ${row.title ?? row.incident_id}`}
                      className="inline-flex items-center gap-1 underline underline-offset-2 hover:text-foreground"
                    >
                      Trace
                      <ExternalLink aria-hidden className="size-3.5" />
                    </a>
                  ) : (
                    <span className="text-muted-foreground">none</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Tokens and dollars from the usage ledger, per day and per incident. */
export function CostsPanel() {
  const [reload, setReload] = useState(0);
  const { data, error } = useGet<CostView>(`/api/dev/costs?days=${DAYS}`, reload);
  return (
    <div className="flex flex-col gap-3">
      {error && <Failed>Could not load the costs: {error.message}</Failed>}
      {!data && !error && <Loading>Loading the costs</Loading>}
      {data && <CostTables costs={data} />}
      <div>
        <Button variant="ghost" size="sm" onClick={() => setReload((n) => n + 1)}>
          <RefreshCw aria-hidden />
          Refresh
        </Button>
      </div>
    </div>
  );
}
