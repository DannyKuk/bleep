import { formatCompact, formatCount, formatDollars, formatShare } from "@/lib/format";
import { useLanguage, useT } from "@/lib/i18n";
import type { UsageView } from "@/lib/api-types";

function Tile({ label, value, note }: { label: string; value: string; note?: string | null }) {
  return (
    <div
      role="group"
      aria-label={label}
      className="min-w-0 rounded-card-sm border border-line bg-surface p-4"
    >
      <p className="text-sm font-bold text-ink-2">{label}</p>
      <p className="mt-1 text-2xl font-extrabold wrap-anywhere tabular-nums">{value}</p>
      {note && <p className="mt-1 text-xs wrap-anywhere text-ink-2">{note}</p>}
    </div>
  );
}

/** The five headline numbers of the range. */
export function UsageTiles({ usage }: { usage: UsageView }) {
  const language = useLanguage();
  const t = useT().usage.tiles;
  const { totals, incidents } = usage;
  const unpriced = totals.unpriced_calls - totals.unpriced_local_calls;
  const spentNote = [
    totals.local_calls === 1
      ? t.oneFree
      : totals.local_calls > 1
        ? t.manyFree(formatCount(totals.local_calls, language))
        : null,
    unpriced > 0 ? t.unpriced(formatCount(unpriced, language)) : null,
  ]
    .filter((part) => part !== null)
    .join(" · ");
  return (
    <div className="grid grid-cols-1 gap-3 min-[480px]:grid-cols-2 lg:grid-cols-5">
      <Tile
        label={t.spent}
        value={formatDollars(totals.cost_usd, language)}
        note={spentNote || null}
      />
      <Tile
        label={t.calls}
        value={formatCount(totals.calls, language)}
        note={t.callsSplit(
          formatCount(totals.incident_calls, language),
          formatCount(totals.chat_calls, language),
        )}
      />
      <Tile
        label={t.tokens}
        value={t.tokensValue(
          formatCompact(totals.tokens_in, language),
          formatCompact(totals.tokens_out, language),
        )}
      />
      <Tile
        label={t.cache}
        value={formatShare(totals.cache_read_tokens, totals.tokens_in, language)}
        note={t.cacheOf}
      />
      <Tile
        label={t.incidents}
        value={formatCount(incidents.count, language)}
        note={
          incidents.average_cost_usd === null
            ? null
            : t.average(formatDollars(incidents.average_cost_usd, language))
        }
      />
    </div>
  );
}
