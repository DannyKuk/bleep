"use client";

import { useState } from "react";

import { niceMax, type Series } from "@/components/usage/usage-data";
import { FIXED_LOCALE, useLanguage, useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

// A bar is at least this wide, plus the 2 px gap; a range too long for the width scrolls.
const MIN_SLOT_PX = 5;

function shortDay(day: string, language: "en" | "de"): string {
  return new Date(`${day}T00:00:00Z`).toLocaleDateString(FIXED_LOCALE[language], {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
}

const totalOf = (series: readonly Series[], day: string) =>
  series.reduce((sum, s) => sum + (s.values.get(day) ?? 0), 0);

function Swatch({ colour }: { colour: string }) {
  return (
    <span
      aria-hidden
      className="inline-block size-2.5 shrink-0 rounded-[3px]"
      style={{ backgroundColor: colour }}
    />
  );
}

function Tooltip({
  day,
  series,
  format,
  placement,
}: {
  day: string;
  series: readonly Series[];
  format: (value: number) => string;
  placement: { left: string; flip: boolean };
}) {
  const language = useLanguage();
  const t = useT().usage.charts;
  const shown = series.filter((s) => (s.values.get(day) ?? 0) > 0).reverse();
  return (
    <div
      aria-hidden
      className={cn(
        "pointer-events-none absolute top-0 z-10 flex max-h-full w-max max-w-64 flex-col gap-1 overflow-hidden rounded-field border border-line bg-surface px-3 py-2 text-xs shadow-sm",
        placement.flip && "-translate-x-full",
      )}
      style={{ left: placement.left }}
    >
      <span className="font-bold">{shortDay(day, language)}</span>
      {shown.length === 0 && <span className="text-ink-2">{format(0)}</span>}
      {shown.map((s) => (
        <span key={s.key} className="flex items-start gap-1.5">
          <span className="mt-0.5">
            <Swatch colour={s.colour} />
          </span>
          <span className="min-w-0 flex-1 wrap-anywhere text-ink-2">{s.label}</span>
          <span className="font-bold tabular-nums">{format(s.values.get(day) ?? 0)}</span>
        </span>
      ))}
      {shown.length > 1 && (
        <span className="flex justify-between gap-3 border-t border-line pt-1">
          <span className="text-ink-2">{t.total}</span>
          <span className="font-bold tabular-nums">{format(totalOf(series, day))}</span>
        </span>
      )}
    </div>
  );
}

function Bars({
  title,
  days,
  series,
  format,
}: {
  title: string;
  days: readonly string[];
  series: readonly Series[];
  format: (value: number) => string;
}) {
  const language = useLanguage();
  const t = useT().usage.charts;
  const [hovered, setHovered] = useState<number | null>(null);
  const top = niceMax(Math.max(0, ...days.map((day) => totalOf(series, day))));
  const hoveredDay = hovered === null ? null : days[hovered];
  const flip = hovered !== null && hovered >= days.length / 2;
  return (
    <div className="flex gap-2">
      <div
        aria-hidden
        className="flex h-48 w-14 shrink-0 flex-col justify-between text-right text-xs text-ink-2 tabular-nums"
      >
        <span className="-mt-1.5">{format(top)}</span>
        <span className="-mb-1.5">{format(0)}</span>
      </div>
      <div
        role="region"
        aria-label={t.barsLabel(title)}
        tabIndex={0}
        className="min-w-0 flex-1 overflow-x-auto"
      >
        <div style={{ minWidth: days.length * MIN_SLOT_PX }}>
          <div className="relative h-48">
            <div aria-hidden className="absolute inset-x-0 top-0 border-t border-line" />
            <div aria-hidden className="absolute inset-x-0 top-1/2 border-t border-line" />
            <div aria-hidden className="absolute inset-x-0 bottom-0 border-t border-line" />
            <div className="absolute inset-0 flex" onMouseLeave={() => setHovered(null)}>
              {days.map((day, i) => {
                const parts = series.filter((s) => (s.values.get(day) ?? 0) > 0);
                return (
                  <div
                    key={day}
                    data-day={day}
                    onMouseEnter={() => setHovered(i)}
                    onPointerDown={() => setHovered(i)}
                    className={cn(
                      "flex min-w-0 flex-1 flex-col justify-end px-px",
                      hovered === i && "bg-soft",
                    )}
                  >
                    <div className="mx-auto flex h-full w-full max-w-6 flex-col-reverse gap-[2px]">
                      {parts.map((s, index) => (
                        <div
                          key={s.key}
                          data-series={s.key}
                          className={cn(
                            "min-h-[2px] w-full shrink-0",
                            index === parts.length - 1 && "rounded-t-[4px]",
                          )}
                          style={{
                            backgroundColor: s.colour,
                            height: `${(((s.values.get(day) ?? 0) / top) * 100).toFixed(2)}%`,
                          }}
                        />
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
            {hoveredDay !== null && (
              <Tooltip
                day={hoveredDay}
                series={series}
                format={format}
                placement={{
                  left: `${(((hovered ?? 0) + (flip ? 1 : 0)) / days.length) * 100}%`,
                  flip,
                }}
              />
            )}
          </div>
          <div aria-hidden className="mt-1 flex justify-between text-xs text-ink-2">
            <span>{shortDay(days[0], language)}</span>
            <span>{shortDay(days[days.length - 1], language)}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function DayTable({
  title,
  days,
  series,
  format,
}: {
  title: string;
  days: readonly string[];
  series: readonly Series[];
  format: (value: number) => string;
}) {
  const t = useT().usage.charts;
  const rows = days.filter((day) => totalOf(series, day) > 0).reverse();
  const head = "px-2 py-1.5 text-right font-medium text-ink-2";
  return (
    <div className="max-h-64 overflow-auto">
      <table aria-label={title} className="w-full text-sm">
        <thead className="sticky top-0 border-b border-line bg-surface">
          <tr>
            <th className="px-2 py-1.5 text-left font-medium text-ink-2">{t.day}</th>
            {series.map((s) => (
              <th key={s.key} className={head}>
                <span className="inline-flex items-center gap-1.5">
                  <Swatch colour={s.colour} />
                  <span className="font-mono text-xs wrap-anywhere">{s.label}</span>
                </span>
              </th>
            ))}
            {series.length > 1 && <th className={head}>{t.total}</th>}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {rows.map((day) => (
            <tr key={day}>
              <td className="px-2 py-1.5 font-mono whitespace-nowrap">{day}</td>
              {series.map((s) => (
                <td key={s.key} className="px-2 py-1.5 text-right tabular-nums">
                  {format(s.values.get(day) ?? 0)}
                </td>
              ))}
              {series.length > 1 && (
                <td className="px-2 py-1.5 text-right font-bold tabular-nums">
                  {format(totalOf(series, day))}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Stacked bars, one per day, with a legend, a tooltip on hover and a table of the same numbers.
 * `series` run bottom to top; a series' colour is its own, never its position.
 */
export function DayChart({
  title,
  days,
  series,
  format,
}: {
  title: string;
  days: readonly string[];
  series: readonly Series[];
  format: (value: number) => string;
}) {
  const t = useT().usage.charts;
  const [view, setView] = useState<"chart" | "table">("chart");
  return (
    <section
      aria-label={title}
      className="min-w-0 rounded-card-sm border border-line bg-surface p-4 md:rounded-card md:p-6"
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-xl font-extrabold">{title}</h2>
        <div role="group" aria-label={t.view} className="flex gap-1">
          {(["chart", "table"] as const).map((id) => (
            <button
              key={id}
              type="button"
              aria-pressed={view === id}
              onClick={() => setView(id)}
              className={cn(
                "min-h-11 rounded-full border px-3 text-sm font-bold focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none pointer-fine:min-h-8",
                view === id
                  ? "border-ink bg-ink text-bg"
                  : "border-line bg-surface text-ink-2 hover:bg-soft",
              )}
            >
              {t[id]}
            </button>
          ))}
        </div>
      </div>
      {series.length > 0 && (
        <ul aria-label={t.legend} className="mb-3 flex flex-wrap gap-x-4 gap-y-1 text-xs">
          {series.map((s) => (
            <li key={s.key} className="flex min-w-0 items-center gap-1.5">
              <Swatch colour={s.colour} />
              <span className="min-w-0 wrap-anywhere text-ink-2">{s.label}</span>
            </li>
          ))}
        </ul>
      )}
      {view === "chart" ? (
        <Bars title={title} days={days} series={series} format={format} />
      ) : (
        <DayTable title={title} days={days} series={series} format={format} />
      )}
    </section>
  );
}
