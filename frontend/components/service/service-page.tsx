"use client";

import { Lock, ThumbsDown, ThumbsUp } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { HealthBadge } from "@/components/overview/health-badge";
import { ENDED } from "@/components/incident/proposal-card";
import { ShortNamePrefix } from "@/components/incident/short-name";
import { IncidentStatusBadge } from "@/components/incident/status-badge";
import { subjectText } from "@/components/incident/incident-list";
import { useOverall } from "@/components/overall-state";
import { Panel } from "@/components/panel";
import { QuietButton } from "@/components/quiet/quiet-button";
import { SaveLockProvider } from "@/components/save-lock";
import { Failed, Loading } from "@/components/status-notes";
import { MemoryPanel } from "@/components/service/memory-panel";
import {
  CheckedUrls,
  FollowedRules,
  nameIn,
  ProtectedContainers,
  ExpectedExits,
  useServiceSettings,
} from "@/components/service/settings-cards";
import { useTimeZone } from "@/components/preferences";
import { ApiError, apiFetch } from "@/lib/api";
import type {
  ContainerView,
  IncidentListItem,
  IncidentVotes,
  QuietView,
  ServiceDetail,
  ServiceView,
} from "@/lib/api-types";
import { formatDateTime, formatSince } from "@/lib/format";
import { MESSAGES, useLanguage, useT } from "@/lib/i18n";
import { sinceWords, untilWords } from "@/lib/quiet-api";
import { hostHref } from "@/lib/routes";
import type { Language, UrlCheck } from "@/lib/settings-types";
import { cn } from "@/lib/utils";
import type { Zone } from "@/lib/zone";

const PAST_INCIDENTS = 20;

interface Loaded<T> {
  data?: T;
  error?: Error;
}

/** One GET, read once when `path` changes. */
function useOnce<T>(path: string, version = 0): Loaded<T> {
  const [state, setState] = useState<Loaded<T>>({});
  useEffect(() => {
    const controller = new AbortController();
    apiFetch<T>(path, { signal: controller.signal })
      .then((data) => setState({ data }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({ error: error instanceof Error ? error : new Error(String(error)) });
      });
    return () => controller.abort();
  }, [path, version]);
  return state;
}

function containerTone(container: ContainerView): string {
  if (container.health === "unhealthy") return "text-down";
  if (container.state === "running") {
    return container.health === "starting" ? "text-warn" : "text-ok";
  }
  if (container.state === "restarting") return "text-warn";
  // Exited 0 or by SIGTERM: stopped on purpose or finished, not a crash.
  if (container.state === "exited" && (container.exit_code === 0 || container.exit_code === 143)) {
    return "text-stop";
  }
  return "text-down";
}

function ContainerRow({ container }: { container: ContainerView }) {
  const t = useT().service;
  const detail =
    container.health === "none" ? container.state : `${container.state}, ${container.health}`;
  return (
    <li
      title={`${container.image} (${container.id.slice(0, 12)})`}
      className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 rounded-row bg-soft px-3 py-2 text-sm"
    >
      <span className="flex items-center gap-1.5 font-mono break-all">
        {container.protected && <Lock aria-hidden className="size-3 shrink-0 text-ink-2" />}
        {container.name}
        {container.protected && <span className="sr-only">{t.protectedNote}</span>}
      </span>
      <span className={cn("font-bold", containerTone(container))}>{detail}</span>
    </li>
  );
}

const FINDINGS = ["diagnosis", "proposal"] as const;

/** The human's right or wrong on the incident's diagnosis and plan; nothing when they gave none. */
function Votes({ votes }: { votes: IncidentVotes }) {
  const t = useT().service;
  const words = {
    diagnosis: { up: t.diagnosisRight, down: t.diagnosisWrong },
    proposal: { up: t.planRight, down: t.planWrong },
  };
  const given = FINDINGS.flatMap((target) => {
    const vote = votes[target];
    return vote ? [{ target, vote }] : [];
  });
  if (given.length === 0) return null;
  return (
    <span className="flex flex-wrap items-center gap-1.5 text-xs font-bold">
      <span className="sr-only">{t.yourFeedback}</span>
      {given.map(({ target, vote }) => {
        const Icon = vote === "up" ? ThumbsUp : ThumbsDown;
        return (
          <span
            key={target}
            className={cn(
              "inline-flex items-center gap-1 rounded-full px-2 py-0.5",
              vote === "up" ? "bg-ok-bg text-ok" : "bg-down-bg text-down",
            )}
          >
            <Icon aria-hidden className="size-3" />
            {words[target][vote]}
          </span>
        );
      })}
    </span>
  );
}

function PastIncidents({ incidents }: { incidents: IncidentListItem[] }) {
  const zone = useTimeZone();
  const language = useLanguage();
  const t = useT();
  if (incidents.length === 0) {
    return <p className="text-sm text-ink-2">{t.service.noIncidents}</p>;
  }
  return (
    <ul aria-label={t.incidents.title} className="flex flex-col gap-1.5">
      {incidents.map((incident) => (
        <li key={incident.id}>
          <Link
            href={`/incidents/${incident.id}`}
            className="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1 rounded-row bg-soft px-3 py-2 hover:bg-line focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          >
            <span className="min-w-0 flex-1 basis-60 text-sm font-bold">
              <ShortNamePrefix name={incident.short_name} />
              {incident.title}
            </span>
            <IncidentStatusBadge status={incident.status} />
            <Votes votes={incident.votes} />
            <span className="font-mono text-xs text-ink-2">{subjectText(incident, t)}</span>
            <time dateTime={incident.opened_at} className="text-xs text-ink-2">
              {formatDateTime(incident.opened_at, zone, language)}
            </time>
          </Link>
        </li>
      ))}
    </ul>
  );
}

/** The one line under a quiet service's title: why Bleep is silent about it, and since or until when. */
function quietNote(quiet: QuietView, zone: Zone, language: Language): string {
  const now = new Date();
  const t = MESSAGES[language].service;
  const until =
    quiet.mode === "leave_alone" || quiet.until === null
      ? null
      : untilWords(new Date(quiet.until), now, zone, language);
  if (quiet.scope === "host") return until === null ? t.hostLeftAlone : t.hostSnoozed(until);
  if (until === null) return t.since(sinceWords(new Date(quiet.since), now, zone, language));
  return t.until(until);
}

/**
 * The line under a service that is still down after its last incident ended: Bleep opens no new
 * incident a minute for a failure it could not fix, so the page says the failure is still there.
 */
function StillFailing({
  service,
  last,
}: {
  service: ServiceView;
  last: IncidentListItem | undefined;
}) {
  const zone = useTimeZone();
  const language = useLanguage();
  const t = useT().service;
  if (service.quiet || service.health !== "down" || service.open_incident_id !== null) return null;
  if (!last || !ENDED.has(last.status)) return null;
  const ended = last.closed_at ?? last.opened_at;
  const checkFails = service.urls.some((url) => url.status === "failing");
  return (
    <p role="note" aria-label={t.stillLabel} className="text-sm text-ink-2">
      {t.stillBefore}{" "}
      <time dateTime={ended} title={formatDateTime(ended, zone, language)}>
        {formatSince(ended, undefined, zone, language)}
      </time>
      :{" "}
      <Link
        href={`/incidents/${last.id}`}
        className="font-bold text-accent hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
      >
        <ShortNamePrefix name={last.short_name} />
        {last.title}
      </Link>
      .{checkFails && t.stillCheck}
    </p>
  );
}

/**
 * A service's page: its health, what Bleep knows, its checks and containers, rules, incidents.
 * Its URLs, rules and protected containers are edited here.
 */
export function ServicePage({ id }: { id: string }) {
  return (
    <SaveLockProvider>
      <Service id={id} />
    </SaveLockProvider>
  );
}

function Service({ id }: { id: string }) {
  const zone = useTimeZone();
  const language = useLanguage();
  const messages = useT();
  const t = messages.service;
  const encoded = encodeURIComponent(id);
  const [reloads, setReloads] = useState(0);
  const overall = useOverall();
  const reload = () => {
    setReloads((n) => n + 1);
    overall.refresh();
  };
  const detail = useOnce<ServiceDetail>(`/api/services/${encoded}`, reloads);
  // Read once, not with the reloads: each read asks Coolify for its applications.
  const suggested = useOnce<UrlCheck[]>(`/api/services/${encoded}/suggested-checks`);
  const incidents = useOnce<IncidentListItem[]>(
    `/api/incidents?service_id=${encoded}&limit=${PAST_INCIDENTS}`,
    reloads,
  );
  const data = detail.data;
  const service = data?.service;
  const missing = detail.error instanceof ApiError && detail.error.status === 404;
  const name = service?.name ?? nameIn(id);
  const { settings, error: settingsError, notice } = useServiceSettings(id, service);
  const shown = service !== undefined || (missing && settings !== undefined);
  const target = { scope: "service", id, name } as const;

  return (
    <div className="flex flex-col gap-4">
      <nav aria-label={messages.host.breadcrumb}>
        <ol className="flex flex-wrap items-center gap-x-2 text-sm text-ink-2">
          <li>
            <Link
              href="/"
              className="inline-flex min-h-11 items-center font-bold text-accent hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
            >
              {messages.nav.home}
            </Link>
          </li>
          {service && (
            <>
              <li aria-hidden>&rsaquo;</li>
              <li>
                <Link
                  href={hostHref(service.host_id)}
                  className="inline-flex min-h-11 items-center font-mono font-bold text-accent hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                >
                  {service.host_id}
                </Link>
              </li>
            </>
          )}
          <li aria-hidden>&rsaquo;</li>
          <li aria-current="page" className="min-w-0 font-bold wrap-anywhere text-ink">
            {name}
          </li>
        </ol>
      </nav>

      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <h1 className="text-3xl font-extrabold wrap-anywhere hyphens-auto md:text-4xl">{name}</h1>
          {service && !service.quiet && <HealthBadge health={service.health} />}
          {service && <QuietButton target={target} quiet={service.quiet} onChanged={reload} />}
        </div>
        {service?.quiet && (
          <p role="note" className="text-sm text-ink-2">
            {quietNote(service.quiet, zone, language)}
          </p>
        )}
        {service && incidents.data && <StillFailing service={service} last={incidents.data[0]} />}
        {service ? (
          <p className="text-sm text-ink-2">
            {t.sourceOn(service.source)} <span className="font-mono">{service.host_id}</span>
            {" · "}
            {messages.counts.containers(service.containers.length)}
            {service.health === "ok" && data?.health_since && (
              <>
                {t.healthySince}
                <time
                  dateTime={data.health_since}
                  title={formatDateTime(data.health_since, zone, language)}
                >
                  {formatSince(data.health_since, undefined, zone, language)}
                </time>
              </>
            )}
          </p>
        ) : (
          <p className="font-mono text-xs text-ink-2">{id}</p>
        )}
        {missing && <p className="text-sm text-ink-2">{t.missing}</p>}
        {detail.error && !missing && <Failed>{t.loadFailed(detail.error.message)}</Failed>}
        {!detail.data && !detail.error && <Loading>{t.loading}</Loading>}
        {settingsError && <Failed>{t.settingsFailed(settingsError.message)}</Failed>}
        {notice && <Failed>{notice}</Failed>}
      </header>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title={t.whatIKnow(name)} className="lg:col-span-2">
          <MemoryPanel serviceId={id} />
        </Panel>
        {shown && (
          <>
            <Panel title={t.urls}>
              <CheckedUrls
                name={name}
                statuses={service?.urls ?? []}
                history={data?.recent_checks}
                settings={settings}
                suggested={suggested.data}
              />
            </Panel>
            <Panel title={t.containers}>
              <div className="flex flex-col gap-4">
                {service && service.containers.length > 0 ? (
                  <ul className="flex flex-col gap-1.5" aria-label={t.containers}>
                    {service.containers.map((container) => (
                      <ContainerRow key={container.id} container={container} />
                    ))}
                  </ul>
                ) : (
                  <p className="text-sm text-ink-2">{t.noContainers}</p>
                )}
                {settings && <ProtectedContainers name={name} settings={settings} />}
                {settings && <ExpectedExits name={name} settings={settings} />}
              </div>
            </Panel>
            <Panel title={t.rules} className="lg:col-span-2">
              <FollowedRules
                name={name}
                rules={data?.rules ?? []}
                hostRules={data?.host_rules ?? []}
                settings={settings}
              />
            </Panel>
          </>
        )}
        <Panel title={t.pastIncidents} className="lg:col-span-2">
          {incidents.data ? (
            <PastIncidents incidents={incidents.data} />
          ) : incidents.error ? (
            <Failed>{t.incidentsFailed(incidents.error.message)}</Failed>
          ) : (
            <Loading>{t.incidentsLoading}</Loading>
          )}
        </Panel>
      </div>
    </div>
  );
}
