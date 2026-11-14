import Link from "next/link";

import { Panel } from "@/components/panel";
import { shareOf } from "@/components/usage/usage-data";
import type { ActionCount, GuardCount, UsageView } from "@/lib/api-types";
import { formatCount, formatDollars } from "@/lib/format";
import { useLanguage, useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

function Count({ label, count }: { label: string; count: number }) {
  const language = useLanguage();
  return (
    <li className="flex items-baseline justify-between gap-3 py-1.5 text-sm">
      <span className="min-w-0 wrap-anywhere">{label}</span>
      <span className="font-bold tabular-nums">{formatCount(count, language)}</span>
    </li>
  );
}

function Cap({ label, spent, cap }: { label: string; spent: number; cap: number }) {
  const language = useLanguage();
  const t = useT().usage.cards;
  const reached = spent >= cap; // a cap of 0 stops everything from the first call
  const share = shareOf(spent, cap);
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 text-sm">
        <span className="font-bold">{label}</span>
        <span className="tabular-nums">
          {t.todayOf(formatDollars(spent, language), formatDollars(cap, language))}
        </span>
      </div>
      {cap > 0 && (
        <div
          role="meter"
          aria-label={label}
          aria-valuemin={0}
          aria-valuemax={cap}
          aria-valuenow={Math.min(spent, cap)}
          className="h-2 overflow-hidden rounded-full bg-soft"
        >
          <div
            className={cn(
              "h-full rounded-full",
              reached ? "bg-down" : share >= 0.8 ? "bg-warn" : "bg-accent",
            )}
            style={{ width: `${(Math.min(share, 1) * 100).toFixed(1)}%` }}
          />
        </div>
      )}
      {reached && <p className="text-xs font-bold text-down">{t.capReached}</p>}
    </div>
  );
}

/** Today against both daily caps, whatever the range, and the range's most expensive incident. */
function CapsCard({ usage }: { usage: UsageView }) {
  const language = useLanguage();
  const t = useT().usage.cards;
  const { today, incidents } = usage;
  const costly = incidents.most_expensive;
  return (
    <Panel title={t.today}>
      <div className="flex flex-col gap-4">
        <Cap label={t.todayIncidents} spent={today.spent_usd} cap={today.daily_cap_usd} />
        <Cap label={t.todayChat} spent={today.chat_spent_usd} cap={today.chat_daily_cap_usd} />
        <div className="border-t border-line pt-3 text-sm">
          <p className="font-bold">{t.mostExpensive}</p>
          {costly ? (
            <p className="mt-1 flex flex-wrap items-baseline justify-between gap-x-3">
              <Link
                href={`/incidents/${costly.incident_id}`}
                className="min-w-0 font-bold wrap-anywhere underline underline-offset-2 hover:text-accent"
              >
                {costly.name}
              </Link>
              <span className="tabular-nums">{formatDollars(costly.cost_usd, language)}</span>
            </p>
          ) : (
            <p className="mt-1 text-ink-2">{t.noIncident}</p>
          )}
        </div>
      </div>
    </Panel>
  );
}

function EndingsCard({ usage }: { usage: UsageView }) {
  const t = useT().usage.cards;
  const { endings, incidents } = usage;
  const rows = [
    ...(Object.keys(t.endingLabels) as (keyof typeof t.endingLabels)[]).map((key) => ({
      key,
      count:
        key === "failed" || key === "open" || key === "other"
          ? endings[key]
          : (endings.outcomes[key] ?? 0),
    })),
  ].filter((row) => row.count > 0);
  return (
    <Panel title={t.endings}>
      {incidents.count === 0 || rows.length === 0 ? (
        <p className="text-sm text-ink-2">{t.noEndings}</p>
      ) : (
        <ul className="divide-y divide-line">
          {rows.map((row) => (
            <Count key={row.key} label={t.endingLabels[row.key]} count={row.count} />
          ))}
        </ul>
      )}
    </Panel>
  );
}

/** The action log by action, each with its count for every result it had. */
function byAction(actions: readonly ActionCount[]): [string, ActionCount[]][] {
  const groups = new Map<string, ActionCount[]>();
  for (const row of actions) groups.set(row.action, [...(groups.get(row.action) ?? []), row]);
  return [...groups.entries()];
}

function ActionsCard({ actions }: { actions: readonly ActionCount[] }) {
  const language = useLanguage();
  const t = useT().usage.cards;
  const labels: Record<string, string | undefined> = t.actionLabels;
  const statuses: Record<string, string | undefined> = t.statusLabels;
  return (
    <Panel title={t.actions}>
      {actions.length === 0 ? (
        <p className="text-sm text-ink-2">{t.noActions}</p>
      ) : (
        <ul className="divide-y divide-line">
          {byAction(actions).map(([action, rows]) => (
            <li key={action} className="py-1.5 text-sm">
              <p className="font-bold wrap-anywhere">{labels[action] ?? action}</p>
              <p className="text-ink-2">
                {rows
                  .map(
                    (row) =>
                      `${statuses[row.status] ?? row.status} ${formatCount(row.count, language)}`,
                  )
                  .join(" · ")}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function GuardCard({ guard }: { guard: readonly GuardCount[] }) {
  const t = useT().usage.cards;
  const labels: Record<string, string | undefined> = t.guardKinds;
  return (
    <Panel title={t.guard}>
      {guard.length === 0 ? (
        <p className="text-sm text-ink-2">{t.noGuard}</p>
      ) : (
        <ul className="divide-y divide-line">
          {guard.map((row) => (
            <Count key={row.kind} label={labels[row.kind] ?? row.kind} count={row.count} />
          ))}
        </ul>
      )}
    </Panel>
  );
}

/** Today against the caps, how incidents ended, what was changed after a yes, what the guard stopped. */
export function UsageCards({ usage, capsOnly = false }: { usage: UsageView; capsOnly?: boolean }) {
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      <CapsCard usage={usage} />
      {!capsOnly && (
        <>
          <EndingsCard usage={usage} />
          <ActionsCard actions={usage.actions} />
          <GuardCard guard={usage.guard} />
        </>
      )}
    </div>
  );
}
