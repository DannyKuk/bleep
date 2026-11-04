import { ArrowLeft } from "lucide-react";
import Link from "next/link";

import { Bleep } from "@/components/bleep/bleep";
import { headline } from "@/components/incident/headline";
import { subjectText } from "@/components/incident/incident-list";
import { IncidentStatusBadge } from "@/components/incident/status-badge";
import { ThemeToggle } from "@/components/theme-toggle";
import { Badge } from "@/components/ui/badge";
import type { IncidentDetail } from "@/lib/api-types";
import { formatDateTime } from "@/lib/format";
import { incidentMood } from "@/lib/mood";
import { serviceHref } from "@/lib/routes";
import type { StreamStatus } from "@/lib/use-incident-stream";
import { cn } from "@/lib/utils";

const STREAM: Record<StreamStatus, { label: string; dot: string }> = {
  loading: { label: "Connecting", dot: "bg-stop" },
  live: { label: "Live", dot: "bg-ok" },
  reconnecting: { label: "Reconnecting", dot: "bg-warn" },
};

function StreamBadge({ status }: { status: StreamStatus }) {
  const { label, dot } = STREAM[status];
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-bold text-ink-2">
      <span aria-hidden className={cn("size-2 rounded-full", dot)} />
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
            Incidents /
          </Link>
          <Subject detail={detail} />
          <span className="font-mono">
            · opened <time dateTime={incident.opened_at}>{formatDateTime(incident.opened_at)}</time>
          </span>
          {incident.tokens > 0 && (
            <span className="font-mono">· {incident.tokens.toLocaleString("en-US")} tokens</span>
          )}
          {incident.cost_usd != null && (
            <span className="font-mono">· ${incident.cost_usd.toFixed(3)}</span>
          )}
        </p>
        <h1
          id="incident-headline"
          className="text-[32px] leading-tight font-extrabold text-balance md:text-4xl"
        >
          <HeadlineWords detail={detail} />
        </h1>
        <p className="text-[17px] text-ink-2">{incident.title}</p>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <IncidentStatusBadge status={incident.status} />
        {outcome && outcome !== incident.status && <Badge>{outcome.replaceAll("_", " ")}</Badge>}
        <StreamBadge status={stream} />
      </div>
    </section>
  );
}

/** The headline, with the subject's name in mono: it is data from the host, not Bleep's words. */
function HeadlineWords({ detail }: { detail: IncidentDetail }) {
  const { lead, name, text } = headline(detail);
  return (
    <>
      {lead}
      {name && <code className="font-mono font-medium">{name}</code>}
      {text}
    </>
  );
}

function Subject({ detail }: { detail: IncidentDetail }) {
  const serviceId = detail.incident.subject.service_id;
  return serviceId ? (
    <Link
      href={serviceHref(serviceId)}
      className="font-mono underline-offset-2 hover:text-ink hover:underline"
    >
      {subjectText(detail.incident)}
    </Link>
  ) : (
    <span className="font-mono">{subjectText(detail.incident)}</span>
  );
}

/**
 * The phone's top of an incident's page: a small header (back, subject and time, theme), which
 * stands in for the app's header on a phone (`AppHeader`), then Bleep at 76 px beside the status
 * pill and the headline. Nothing else, so the plan and its pinned bar come soon.
 */
export function PhoneIncidentHero({ detail }: { detail: IncidentDetail }) {
  const { incident, diagnosis } = detail;
  return (
    <>
      <div className="flex items-center gap-3">
        <Link
          href="/incidents"
          aria-label="Back to incidents"
          className="grid size-11 shrink-0 place-items-center rounded-full border border-line bg-surface hover:bg-soft focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          <ArrowLeft aria-hidden className="size-5" />
        </Link>
        <p className="flex min-w-0 flex-1 flex-wrap gap-x-1.5 text-xs text-ink-2">
          <Subject detail={detail} />
          <span className="font-mono">
            · <time dateTime={incident.opened_at}>{formatDateTime(incident.opened_at)}</time>
          </span>
        </p>
        <ThemeToggle />
      </div>
      <section aria-labelledby="incident-headline" className="flex items-center gap-3.5">
        <Bleep mood={incidentMood(incident.status, diagnosis?.unsure ?? false)} size={76} />
        <div className="flex min-w-0 flex-col items-start gap-1.5">
          <IncidentStatusBadge status={incident.status} />
          <h1 id="incident-headline" className="text-[22px] leading-tight font-extrabold">
            <HeadlineWords detail={detail} />
          </h1>
        </div>
      </section>
    </>
  );
}
