import type { DayUsage, ModelUsage, UsageRange } from "@/lib/api-types";

/** `day` ("2026-11-05") moved by `days`. Plain calendar arithmetic, in UTC, so no zone shifts it. */
export function addDays(day: string, days: number): string {
  const [year, month, date] = day.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, date + days)).toISOString().slice(0, 10);
}

/**
 * The days a chart spans, oldest first: `range` days ending on today. The API's days are the
 * machine's local days and `today` is the browser's, so the axis ends at the later of today and
 * the latest day with a call; a zone difference then never cuts the newest bar off.
 */
export function dayAxis(range: UsageRange, today: string, rows: readonly DayUsage[]): string[] {
  const last = rows.reduce((latest, row) => (row.day > latest ? row.day : latest), today);
  return Array.from({ length: range }, (_, i) => addDays(last, i - range + 1));
}

/** The top of a chart's value axis: the next 1, 2 or 5 times a power of ten above `max`. */
export function niceMax(max: number): number {
  if (!(max > 0)) return 1;
  const power = 10 ** Math.floor(Math.log10(max));
  const fraction = max / power;
  const step = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10;
  return step * power;
}

/** One stack of a day chart: a colour, a name and a value for each day that has one. */
export interface Series {
  key: string;
  label: string;
  /** A CSS colour, a token. */
  colour: string;
  values: ReadonlyMap<string, number>;
}

export const OTHER_KEY = "other";
export const OTHER_COLOUR = "var(--stop)";

/**
 * A series for each model that had a call, in colour order, the models without a colour slot
 * folded into one "other" series. `slots` is the model's chart colour (`modelSlots`).
 */
export function modelSeries(
  rows: readonly DayUsage[],
  slots: Readonly<Record<string, number | null>>,
  value: (row: DayUsage) => number,
  otherLabel: string,
): Series[] {
  const byKey = new Map<string, Series & { values: Map<string, number> }>();
  for (const row of rows) {
    const slot = slots[row.model] ?? null;
    const key = slot === null ? OTHER_KEY : `model-${slot}`;
    let series = byKey.get(key);
    if (!series) {
      series = {
        key,
        label: slot === null ? otherLabel : row.model,
        colour: slot === null ? OTHER_COLOUR : `var(--viz-${slot})`,
        values: new Map(),
      };
      byKey.set(key, series);
    }
    series.values.set(row.day, (series.values.get(row.day) ?? 0) + value(row));
  }
  return [...byKey.values()].sort((a, b) => order(a.key) - order(b.key));
}

const order = (key: string) => (key === OTHER_KEY ? 99 : Number(key.slice("model-".length)));

/** The share of `part` in `whole`, 0 to 1; 0 when the whole is nothing. */
export const shareOf = (part: number, whole: number) => (whole > 0 ? part / whole : 0);

/** Calls that report no cache read or write: shown as "–", not as a zero. */
export const reportsNoCache = (row: ModelUsage) =>
  row.cache_read_tokens === 0 && row.cache_write_tokens === 0;
