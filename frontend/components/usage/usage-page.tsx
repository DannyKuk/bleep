"use client";

import { Loader2, TriangleAlert } from "lucide-react";
import { useCallback, useState } from "react";

import { DayChart } from "@/components/usage/day-chart";
import { ModelsTable } from "@/components/usage/models-table";
import { UsageCards } from "@/components/usage/usage-cards";
import { dayAxis, modelSeries, OTHER_COLOUR, type Series } from "@/components/usage/usage-data";
import { UsageTiles } from "@/components/usage/usage-tiles";
import { usePoll } from "@/hooks/use-poll";
import { apiFetch } from "@/lib/api";
import type { UsageRange, UsageView } from "@/lib/api-types";
import { formatCount, formatDollars } from "@/lib/format";
import { useLanguage, useT } from "@/lib/i18n";
import { modelSlots } from "@/lib/model-colours";
import { cn } from "@/lib/utils";

const RANGES: readonly UsageRange[] = [1, 7, 30, 90];
const DEFAULT_RANGE: UsageRange = 30;
const POLL_MS = 60_000;

const nothingHappened = (usage: UsageView) =>
  usage.totals.calls === 0 &&
  usage.incidents.count === 0 &&
  usage.actions.length === 0 &&
  usage.guard.length === 0;

function RangePicker({
  range,
  onChange,
}: {
  range: UsageRange;
  onChange: (range: UsageRange) => void;
}) {
  const t = useT().usage;
  return (
    <div role="group" aria-label={t.range} className="flex flex-wrap gap-2">
      {RANGES.map((days) => (
        <button
          key={days}
          type="button"
          aria-pressed={range === days}
          onClick={() => onChange(days)}
          className={cn(
            "min-h-11 rounded-full border px-4 text-sm font-bold focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
            range === days
              ? "border-ink bg-ink text-bg"
              : "border-line bg-surface text-ink-2 hover:bg-soft",
          )}
        >
          {t.ranges[days]}
        </button>
      ))}
    </div>
  );
}

function Charts({ usage }: { usage: UsageView }) {
  const language = useLanguage();
  const t = useT().usage;
  const slots = modelSlots(usage.models.map((row) => row.model));
  const days = dayAxis(usage.days, usage.through);
  const spend = modelSeries(usage.per_day, slots, (row) => row.cost_usd, t.charts.other);
  const calls: Series[] = [
    {
      key: "incidents",
      label: t.charts.incidents,
      colour: "var(--viz-incidents)",
      values: sumPerDay(usage, (row) => row.incident_calls),
    },
    {
      key: "chat",
      label: t.charts.chat,
      colour: "var(--viz-chat)",
      values: sumPerDay(usage, (row) => row.chat_calls),
    },
  ];
  const anySpend = usage.per_day.some((row) => row.cost_usd > 0);
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      {anySpend ? (
        <DayChart
          title={t.charts.spend}
          days={days}
          series={spend}
          format={(value) => formatDollars(value, language)}
        />
      ) : (
        <section
          aria-label={t.charts.spend}
          className="min-w-0 rounded-card-sm border border-line bg-surface p-4 md:rounded-card md:p-6"
        >
          <h2 className="mb-3 text-xl font-extrabold">{t.charts.spend}</h2>
          <p className="text-sm text-ink-2">{t.charts.noSpend}</p>
        </section>
      )}
      <DayChart
        title={t.charts.calls}
        days={days}
        series={calls}
        format={(value) => formatCount(value, language)}
      />
    </div>
  );
}

function sumPerDay(usage: UsageView, value: (row: UsageView["per_day"][number]) => number) {
  const sums = new Map<string, number>();
  for (const row of usage.per_day) sums.set(row.day, (sums.get(row.day) ?? 0) + value(row));
  return sums;
}

/** The page's numbers for one range. */
export function UsageContent({ usage }: { usage: UsageView }) {
  const t = useT().usage;
  if (nothingHappened(usage)) {
    return (
      <div className="flex flex-col gap-4">
        <p role="status" className="text-sm text-ink-2">
          {t.empty}
        </p>
        <UsageCards usage={usage} capsOnly />
      </div>
    );
  }
  const slots = modelSlots(usage.models.map((row) => row.model));
  const colourOf = (model: string) => (slots[model] ? `var(--viz-${slots[model]})` : OTHER_COLOUR);
  return (
    <div className="flex flex-col gap-4">
      <UsageTiles usage={usage} />
      {usage.totals.calls === 0 ? (
        <p role="status" className="text-sm text-ink-2">
          {t.noCalls}
        </p>
      ) : (
        <>
          <ModelsTable models={usage.models} colourOf={colourOf} />
          <Charts usage={usage} />
        </>
      )}
      <UsageCards usage={usage} />
    </div>
  );
}

/** One range's numbers, read now and every minute. Keyed by the range, so a range never shows
 * the data or the failure of another. */
function UsageRead({ range }: { range: UsageRange }) {
  const messages = useT();
  const t = messages.usage;
  const fetchUsage = useCallback(
    (signal: AbortSignal) => apiFetch<UsageView>(`/api/usage?days=${range}`, { signal }),
    [range],
  );
  const { data, error } = usePoll(fetchUsage, POLL_MS);
  if (!data) {
    return error ? (
      <p role="alert" className="flex items-center gap-2 text-sm text-down">
        <TriangleAlert aria-hidden className="size-4" />
        {t.loadFailed(error.message)}
      </p>
    ) : (
      <p role="status" className="flex items-center gap-2 text-sm text-ink-2">
        <Loader2 aria-hidden className="size-4 animate-spin" />
        {t.loading}
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-4">
      {error && (
        <p role="alert" className="flex items-center gap-2 text-xs text-health-degraded">
          <TriangleAlert aria-hidden className="size-3.5" />
          {messages.common.refreshFailed(error.message)}
        </p>
      )}
      <UsageContent usage={data} />
    </div>
  );
}

/** The Usage page: a range picker and what Bleep spent and did over that range. */
export function UsagePage() {
  const t = useT().usage;
  const [range, setRange] = useState<UsageRange>(DEFAULT_RANGE);
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-2xl font-extrabold">{t.title}</h1>
        <p className="mt-1 text-sm text-ink-2">{t.intro}</p>
      </div>
      <RangePicker range={range} onChange={setRange} />
      <UsageRead key={range} range={range} />
    </div>
  );
}
