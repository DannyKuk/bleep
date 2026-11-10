import { ArrowLeft } from "lucide-react";
import Link from "next/link";

import { Bleep } from "@/components/bleep/bleep";
import { headline } from "@/components/incident/headline";
import { subjectText } from "@/components/incident/incident-list";
import { ShortName } from "@/components/incident/short-name";
import { IncidentStatusBadge } from "@/components/incident/status-badge";
import { ThemeToggle } from "@/components/theme-toggle";
import { useTimeZone } from "@/components/preferences";
import { Badge } from "@/components/ui/badge";
import type { IncidentDetail } from "@/lib/api-types";
import { formatCount, formatDateTime } from "@/lib/format";
import { useLanguage, useT } from "@/lib/i18n";
import { incidentMood } from "@/lib/mood";
import { serviceHref } from "@/lib/routes";
import type { StreamStatus } from "@/lib/use-incident-stream";
import { cn } from "@/lib/utils";

const DOT: Record<StreamStatus, string> = {
  loading: "bg-stop",
  live: "bg-ok",
  reconnecting: "bg-warn",
};

function StreamBadge({ status }: { status: StreamStatus }) {
  const label = useT().incidents.stream[status];
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-bold text-ink-2">
      <span aria-hidden className={cn("size-2 rounded-full", DOT[status])} />
      {label}
    </span>
  );
}

/**
 * The top of an incident's page: Bleep in the incident's mood, a headline for the moment, the
 * incident's title, its subject, time and cost, and the status pill. Bleep is decorative here:
 * the status pill and the headline say the same in words.
 */
export function IncidentHero({ detail, stream }: { detail: IncidentDetail; stream: StreamStatus }) {
  const { incident, diagnosis, outcome } = detail;
  const mood = incidentMood(incident.status, diagnosis?.unsure ?? false);
  const zone = useTimeZone();
  const language = useLanguage();
  const t = useT();
  return (
    <section
      aria-labelledby="incident-headline"
      className="flex flex-wrap items-center gap-x-7 gap-y-4"
    >
      <Bleep mood={mood} size={112} />
      <div className="flex min-w-0 flex-[1_1_420px] flex-col gap-2">
        <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[13px] text-ink-2">
          <Link
            href="/incidents"
            className="rounded-full text-sm font-bold hover:text-ink focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          >
            {t.incidents.crumb}
          </Link>
          <Subject detail={detail} />
          <span className="font-mono">
            · {t.incidents.opened}{" "}
            <time dateTime={incident.opened_at}>
              {formatDateTime(incident.opened_at, zone, language)}
            </time>
          </span>
          {incident.tokens > 0 && (
            <span className="font-mono">
              · {t.incidents.tokens(formatCount(incident.tokens, language))}
            </span>
          )}
          {incident.cost_usd != null && (
            <span className="font-mono">· ${incident.cost_usd.toFixed(3)}</span>
          )}
        </p>
        <h1
          id="incident-headline"
          className="text-[32px] leading-tight font-extrabold text-balance wrap-anywhere hyphens-auto md:text-4xl"
        >
          <HeadingName name={incident.short_name} className="text-base" />
          <HeadlineWords detail={detail} />
        </h1>
        <p className="text-[17px] text-ink-2">{incident.title}</p>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <IncidentStatusBadge status={incident.status} />
        {outcome && outcome !== incident.status && <Badge>{t.outcome[outcome]}</Badge>}
        <StreamBadge status={stream} />
      </div>
    </section>
  );
}

/** The headline, with the subject's name in mono: it is data from the host, not Bleep's words. */
function HeadlineWords({ detail }: { detail: IncidentDetail }) {
  const { lead, name, text } = headline(detail, useT());
  return (
    <>
      {lead}
      {name && <code className="font-mono font-medium">{name}</code>}
      {text}
    </>
  );
}

/**
 * The incident's short name ("shop #14", as Telegram shows it) on its own line above the
 * headline. The space after it keeps the heading's spoken name two phrases.
 */
function HeadingName({ name, className }: { name: string | null; className: string }) {
  if (!name) return null;
  return (
    <>
      <ShortName name={name} className={cn("block", className)} />{" "}
    </>
  );
}

function Subject({ detail }: { detail: IncidentDetail }) {
  const t = useT();
  const serviceId = detail.incident.subject.service_id;
  return serviceId ? (
    <Link
      href={serviceHref(serviceId)}
      className="font-mono underline-offset-2 hover:text-ink hover:underline"
    >
      {subjectText(detail.incident, t)}
    </Link>
  ) : (
    <span className="font-mono">{subjectText(detail.incident, t)}</span>
  );
}

/**
 * The phone's top of an incident's page: a small header (back, subject and time, theme), which
 * stands in for the app's header on a phone (`AppHeader`), then Bleep at 76 px beside the status
 * pill and the headline. Nothing else, so the plan and its pinned bar come soon.
 */
export function PhoneIncidentHero({ detail }: { detail: IncidentDetail }) {
  const zone = useTimeZone();
  const language = useLanguage();
  const t = useT();
  const { incident, diagnosis } = detail;
  return (
    <>
      <div className="flex items-center gap-3">
        <Link
          href="/incidents"
          aria-label={t.incidents.back}
          className="grid size-11 shrink-0 place-items-center rounded-full border border-line bg-surface hover:bg-soft focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          <ArrowLeft aria-hidden className="size-5" />
        </Link>
        <p className="flex min-w-0 flex-1 flex-wrap gap-x-1.5 text-xs text-ink-2">
          <Subject detail={detail} />
          <span className="font-mono">
            ·{" "}
            <time dateTime={incident.opened_at}>
              {formatDateTime(incident.opened_at, zone, language)}
            </time>
          </span>
        </p>
        <ThemeToggle />
      </div>
      <section aria-labelledby="incident-headline" className="flex items-center gap-3.5">
        <Bleep mood={incidentMood(incident.status, diagnosis?.unsure ?? false)} size={76} />
        <div className="flex min-w-0 flex-col items-start gap-1.5">
          <IncidentStatusBadge status={incident.status} />
          <h1
            id="incident-headline"
            className="text-[22px] leading-tight font-extrabold wrap-anywhere hyphens-auto"
          >
            <HeadingName name={incident.short_name} className="text-sm" />
            <HeadlineWords detail={detail} />
          </h1>
        </div>
      </section>
    </>
  );
}
