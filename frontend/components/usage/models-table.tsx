import { reportsNoCache, shareOf } from "@/components/usage/usage-data";
import type { ModelUsage } from "@/lib/api-types";
import { formatCompact, formatCount, formatDollars, formatShare } from "@/lib/format";
import { useLanguage, useT } from "@/lib/i18n";

const cell = "px-2 py-1.5 text-right tabular-nums whitespace-nowrap";
const head = "px-2 py-1.5 text-right font-medium text-ink-2 whitespace-nowrap";

function CostCell({ row }: { row: ModelUsage }) {
  const language = useLanguage();
  const t = useT().usage.models;
  if (row.provider === "local") return <>{t.free}</>;
  if (row.unpriced_calls >= row.calls) return <>{t.unknown}</>;
  const usd = formatDollars(row.cost_usd, language);
  return (
    <>
      {row.unpriced_calls > 0
        ? t.withUnpriced(usd, formatCount(row.unpriced_calls, language))
        : usd}
    </>
  );
}

/**
 * Every model of the range with the provider it was called through: the check that a call went
 * to the model and provider the person set up. `colourOf` is the model's chart colour.
 */
export function ModelsTable({
  models,
  colourOf,
}: {
  models: readonly ModelUsage[];
  colourOf: (model: string) => string;
}) {
  const language = useLanguage();
  const t = useT().usage.models;
  const costTotal = models.reduce((sum, row) => sum + row.cost_usd, 0);
  return (
    <section
      aria-labelledby="usage-models"
      className="min-w-0 rounded-card-sm border border-line bg-surface p-4 md:rounded-card md:p-6"
    >
      <h2 id="usage-models" className="mb-3 text-xl font-extrabold">
        {t.title}
      </h2>
      <div className="overflow-x-auto">
        <table aria-label={t.title} className="w-full text-sm">
          <thead className="border-b border-line">
            <tr>
              <th className="px-2 py-1.5 text-left font-medium text-ink-2">{t.model}</th>
              <th className="px-2 py-1.5 text-left font-medium text-ink-2">{t.provider}</th>
              <th className={head}>{t.calls}</th>
              <th className={head}>{t.tokensIn}</th>
              <th className={head}>{t.tokensOut}</th>
              <th className={head}>{t.cached}</th>
              <th className={head}>{t.cost}</th>
              <th className="w-28 px-2 py-1.5 text-left font-medium text-ink-2">{t.share}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {models.map((row) => {
              const share = shareOf(row.cost_usd, costTotal);
              return (
                <tr key={`${row.provider}/${row.model}`}>
                  <td className="px-2 py-1.5">
                    <span className="flex items-start gap-1.5">
                      <span
                        aria-hidden
                        className="mt-1 inline-block size-2.5 shrink-0 rounded-[3px]"
                        style={{ backgroundColor: colourOf(row.model) }}
                      />
                      <span className="min-w-40 font-mono text-xs break-all">{row.model}</span>
                    </span>
                  </td>
                  <td className="px-2 py-1.5 whitespace-nowrap">{t.providers[row.provider]}</td>
                  <td className={cell}>{formatCount(row.calls, language)}</td>
                  <td className={cell}>{formatCompact(row.tokens_in, language)}</td>
                  <td className={cell}>{formatCompact(row.tokens_out, language)}</td>
                  <td className={cell}>
                    {reportsNoCache(row) ? "–" : formatCompact(row.cache_read_tokens, language)}
                  </td>
                  <td className={cell}>
                    <CostCell row={row} />
                  </td>
                  <td className="px-2 py-1.5">
                    {share > 0 && (
                      <div className="flex items-center gap-2">
                        <div aria-hidden className="h-2 w-16 overflow-hidden rounded-full bg-soft">
                          <div
                            className="h-full rounded-full bg-accent"
                            style={{ width: `${(share * 100).toFixed(1)}%` }}
                          />
                        </div>
                        <span className="text-xs text-ink-2 tabular-nums">
                          {formatShare(row.cost_usd, costTotal, language)}
                        </span>
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-xs text-ink-2">{t.note}</p>
    </section>
  );
}
