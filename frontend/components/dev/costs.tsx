"use client";

import { ExternalLink, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Failed, formatUsd, Loading, useGet } from "@/components/dev/status";
import { Button } from "@/components/ui/button";
import type { CostView, ModelCost, Usage } from "@/lib/api-types";
import { formatCount } from "@/lib/format";
import { MESSAGES, useLanguage, useT } from "@/lib/i18n";
import type { Language } from "@/lib/settings-types";

const DAYS = 30;

/** Only an http(s) link is rendered as one; any other is shown as text. */
export const isWebLink = (url: string | null): url is string =>
  url !== null && /^https?:\/\//i.test(url);

/** Today's spend against the cap, and the calls today it leaves out. */
export function todayText(
  { spent_today_usd, daily_cap_usd, unpriced_today }: CostView,
  language: Language,
): string {
  const t = MESSAGES[language].dev.costs;
  const spent = t.today(formatUsd(spent_today_usd, language), formatUsd(daily_cap_usd, language));
  if (unpriced_today === 0) return spent;
  const calls =
    unpriced_today === 1 ? t.oneUnpriced : t.manyUnpriced(formatCount(unpriced_today, language));
  return `${spent} ${calls}`;
}

/** The cost, with how many calls it leaves out because their price is unknown. */
export function costText({ usd, unpriced }: Usage, language: Language): string {
  const text = formatUsd(usd, language);
  return unpriced > 0
    ? MESSAGES[language].dev.costs.withUnpriced(text, formatCount(unpriced, language))
    : text;
}

const cell = "px-2 py-1.5 text-right tabular-nums whitespace-nowrap";
const head = "px-2 py-1.5 text-right font-medium text-muted-foreground whitespace-nowrap";

function UsageCells({ usage }: { usage: Usage }) {
  const language = useLanguage();
  return (
    <>
      <td className={cell}>{formatCount(usage.calls, language)}</td>
      <td className={cell}>{formatCount(usage.tokens_in, language)}</td>
      <td className={cell}>{formatCount(usage.tokens_out, language)}</td>
      <td className={cell}>{costText(usage, language)}</td>
    </>
  );
}

/** The prompt overrides the incident's run started with, each with the start of its hash. */
function OverrideList({ overrides }: { overrides: Record<string, string> | null }) {
  const t = useT().dev.costs;
  if (overrides === null) return <span className="text-muted-foreground">{t.notRecorded}</span>;
  const names = Object.keys(overrides).sort();
  if (names.length === 0) return <span className="text-muted-foreground">{t.none}</span>;
  return (
    <ul className="flex flex-col gap-0.5">
      {names.map((name) => (
        <li key={name} className="font-mono text-xs">
          <span title={t.overrideVersion(overrides[name])}>{name}</span>{" "}
          <span className="text-muted-foreground">{overrides[name].slice(0, 8)}</span>
        </li>
      ))}
    </ul>
  );
}

function UsageHeads() {
  const t = useT().dev.costs;
  return (
    <>
      <th className={head}>{t.calls}</th>
      <th className={head}>{t.tokensIn}</th>
      <th className={head}>{t.tokensOut}</th>
      <th className={head}>{t.cost}</th>
    </>
  );
}

/** The models' table: each model's calls over the days covered, with what it did today. */
function ModelTable({ costs }: { costs: CostView }) {
  const t = useT().dev.costs;
  const language = useLanguage();
  const today = new Map<string, ModelCost>(costs.models_today.map((row) => [row.model, row]));
  return (
    <div className="overflow-x-auto">
      <table aria-label={t.perModel} className="w-full text-sm">
        <thead className="border-b border-border">
          <tr>
            <th className="px-2 py-1.5 text-left font-medium text-muted-foreground">{t.model}</th>
            <UsageHeads />
            <th className={head}>{t.todayColumn}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {costs.models.length === 0 && (
            <tr>
              <td colSpan={6} className="px-2 py-3 text-muted-foreground">
                {t.noModels(DAYS)}
              </td>
            </tr>
          )}
          {costs.models.map((row) => {
            const now = today.get(row.model);
            return (
              <tr key={row.model}>
                <td className="px-2 py-1.5 font-mono text-xs break-all">{row.model}</td>
                <UsageCells usage={row} />
                <td className={cell}>
                  {now ? (
                    `${formatCount(now.calls, language)} / ${costText(now, language)}`
                  ) : (
                    <span className="text-muted-foreground">{t.none}</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function CostTables({ costs }: { costs: CostView }) {
  const t = useT().dev.costs;
  const language = useLanguage();
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm">{todayText(costs, language)}</p>
      <div className="overflow-x-auto">
        <table aria-label={t.perDay} className="w-full text-sm">
          <thead className="border-b border-border">
            <tr>
              <th className="px-2 py-1.5 text-left font-medium text-muted-foreground">{t.day}</th>
              <UsageHeads />
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {costs.days.length === 0 && (
              <tr>
                <td colSpan={5} className="px-2 py-3 text-muted-foreground">
                  {t.noDays(DAYS)}
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
      <ModelTable costs={costs} />
      <div className="overflow-x-auto">
        <table aria-label={t.perIncident} className="w-full text-sm">
          <thead className="border-b border-border">
            <tr>
              <th className="px-2 py-1.5 text-left font-medium text-muted-foreground">
                {t.incident}
              </th>
              <UsageHeads />
              <th className={head}>{t.trace}</th>
              <th className="px-2 py-1.5 text-left font-medium text-muted-foreground">
                {t.models}
              </th>
              <th className="px-2 py-1.5 text-left font-medium text-muted-foreground">
                {t.overrides}
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {costs.incidents.length === 0 && (
              <tr>
                <td colSpan={8} className="px-2 py-3 text-muted-foreground">
                  {t.noIncidents}
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
                      aria-label={t.traceOf(row.title ?? row.incident_id)}
                      className="inline-flex items-center gap-1 underline underline-offset-2 hover:text-foreground"
                    >
                      {t.trace}
                      <ExternalLink aria-hidden className="size-3.5" />
                    </a>
                  ) : row.trace_url ? (
                    <span className="font-mono text-xs break-all whitespace-normal">
                      {row.trace_url}
                    </span>
                  ) : (
                    <span className="text-muted-foreground">{t.none}</span>
                  )}
                </td>
                <td className="px-2 py-1.5">
                  <ul className="flex flex-col gap-0.5">
                    {row.models.map((model) => (
                      <li key={model} className="font-mono text-xs break-all">
                        {model}
                      </li>
                    ))}
                  </ul>
                </td>
                <td className="px-2 py-1.5">
                  <OverrideList overrides={row.prompt_overrides} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {hasUnpriced(costs) && <p className="text-xs text-muted-foreground">{t.note}</p>}
    </div>
  );
}

const hasUnpriced = (costs: CostView) =>
  costs.unpriced_today > 0 ||
  costs.days.some((day) => day.unpriced > 0) ||
  costs.incidents.some((row) => row.unpriced > 0);

/** Tokens and dollars from the usage ledger, per day, per model and per incident. */
export function CostsPanel() {
  const t = useT().dev.costs;
  const [reload, setReload] = useState(0);
  const { data, error } = useGet<CostView>(`/api/dev/costs?days=${DAYS}`, reload);
  return (
    <div className="flex flex-col gap-3">
      {error && <Failed>{t.loadFailed(error.message)}</Failed>}
      {!data && !error && <Loading>{t.loading}</Loading>}
      {data && <CostTables costs={data} />}
      <div>
        <Button wrap variant="ghost" size="sm" onClick={() => setReload((n) => n + 1)}>
          <RefreshCw aria-hidden />
          {t.refresh}
        </Button>
      </div>
    </div>
  );
}
