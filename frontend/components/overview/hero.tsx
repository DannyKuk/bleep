import { ArrowRight, MessageCircle } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { Bleep } from "@/components/bleep/bleep";
import { useAskBleep } from "@/components/chat/chat-opener";
import { isRefused } from "@/components/incident/headline";
import { ShortName, ShortNamePrefix } from "@/components/incident/short-name";
import { IncidentStatusBadge } from "@/components/incident/status-badge";
import { HealthBadge } from "@/components/overview/health-badge";
import {
  joinNames,
  unwatchedDetail,
  watchedNames,
  watchReason,
  type Situation,
} from "@/components/overview/situation";
import { useTimeZone } from "@/components/preferences";
import type { HostView, Incident, IncidentDetail, PlanStep, ServiceView } from "@/lib/api-types";
import { formatDateTime, formatTime, showInvisible } from "@/lib/format";
import { type Messages, MESSAGES, useLanguage, useT } from "@/lib/i18n";
import type { Mood } from "@/lib/mood";
import { serviceHref } from "@/lib/routes";
import type { Language } from "@/lib/settings-types";
import type { Zone } from "@/lib/zone";

const PLAN_STEPS_SHOWN = 2;

interface Words {
  tag: ReactNode;
  headline: string;
  body: ReactNode;
  /** Monospace detail under the body, such as a host's error. */
  detail?: string;
  link?: { href: string; label: string };
  /** A button in the link's place, such as opening the chat panel. */
  action?: { label: string; onClick: () => void };
}

type Ask = (text: string) => void;

/** The subject's name and health: a service's from the services list, a host's from the hosts. */
function subjectOf(
  incident: Incident,
  hosts: readonly HostView[],
  services: readonly ServiceView[],
) {
  const { kind, host_id, service_id } = incident.subject;
  if (kind === "service" && service_id) {
    const found = services.find((service) => service.id === service_id);
    return { name: found?.name ?? service_id.split(":").pop()!, health: found?.health };
  }
  const found = hosts.find((host) => host.id === host_id);
  return { name: found?.name ?? host_id, health: found?.health };
}

/** "I want to start shop-db, then restart shop-web-1." with the targets in mono. */
function PlanLine({ steps }: { steps: readonly PlanStep[] }) {
  const messages = useT();
  const t = messages.overview;
  const shown = steps.slice(0, PLAN_STEPS_SHOWN);
  const more = steps.length - shown.length;
  return (
    <>
      {t.planLead}
      {shown.map((step, index) => {
        const [before, after] = t.planVerbs[step.action];
        return (
          <span key={index}>
            {index > 0 && t.planThen}
            {before}
            <code className="font-mono">{showInvisible(step.target)}</code>
            {after}
          </span>
        );
      })}
      {more > 0 && t.planMore(messages.counts.moreSteps(more))}
      {t.planEnd}
    </>
  );
}

/** The plan put to the human: the pending approval's, else the run's latest. */
function planOf(detail: IncidentDetail | undefined) {
  if (!detail) return null;
  return detail.approval?.status === "pending" ? detail.approval.proposal : detail.proposal;
}

function TitleLine({ incident }: { incident: Incident }) {
  return (
    <>
      <ShortNamePrefix name={incident.short_name} />
      {incident.title}
    </>
  );
}

function waitingWords(
  incident: Incident,
  detail: IncidentDetail | undefined,
  trouble: string,
  t: Messages,
) {
  const plan = planOf(detail);
  const unsure = detail?.diagnosis?.unsure === true;
  let reason = t.overview.needYou;
  let body: ReactNode = <TitleLine incident={incident} />;
  if (plan && isRefused(plan)) {
    reason = t.overview.refusedPlan;
  } else if (plan?.kind === "no_action" && plan.fix_runnable === true) {
    reason = t.overview.couldRun;
  } else if (plan?.kind === "no_action") {
    reason = unsure ? t.overview.noFixUnsure : t.overview.noFixSure;
  } else if (plan) {
    reason = unsure ? t.overview.planUnsure : t.overview.planSure;
    body = <PlanLine steps={plan.steps} />;
  }
  return { headline: `${trouble} ${reason}`, body };
}

function incidentWords(
  situation: Extract<Situation, { kind: "incident" }>,
  hosts: readonly HostView[],
  services: readonly ServiceView[],
  t: Messages,
): Words {
  const { incident, detail } = situation;
  const subject = subjectOf(incident, hosts, services);
  const trouble = `${subject.name} ${t.overview.trouble[subject.health ?? "unknown"]}.`;
  const href = `/incidents/${incident.id}`;
  const tag = <IncidentStatusBadge status={incident.status} />;
  if (incident.status === "awaiting_approval") {
    return {
      tag,
      ...waitingWords(incident, detail, trouble, t),
      link: { href, label: t.overview.showPlan },
    };
  }
  if (incident.status === "failed") {
    return {
      tag,
      headline: t.overview.couldNotFinish(subject.name),
      body: <TitleLine incident={incident} />,
      link: { href, label: t.overview.showWhat },
    };
  }
  return {
    tag,
    headline: `${trouble} ${t.overview.onIt}`,
    body: <TitleLine incident={incident} />,
    link: { href, label: t.overview.followAlong },
  };
}

function unreachableWords(
  situation: Extract<Situation, { kind: "unreachable" }>,
  services: readonly ServiceView[],
  zone: Zone,
  language: Language,
): Words {
  const t = MESSAGES[language];
  const { host, incident } = situation;
  const names = services.filter((service) => service.host_id === host.id).map((s) => s.name);
  const since = host.last_seen
    ? t.overview.lastHeard(formatDateTime(host.last_seen, zone, language))
    : t.overview.notReached;
  const blind = names.length > 0 ? t.overview.blind(joinNames(names, t)) : "";
  // Only while the watcher reports it waits out the host's window: then an incident follows.
  const opens = incident ? null : host.watching?.opens_after;
  const watch = opens ? ` ${t.overview.lookAfterHost(formatTime(opens, zone, language))}` : "";
  return {
    tag: <HealthBadge health="down" label={t.health.unreachable} />,
    headline: t.overview.cannotReach(host.name),
    body: `${since}${blind}.${watch}`,
    detail: host.error ?? undefined,
    link: incident
      ? { href: `/incidents/${incident.id}`, label: t.overview.followAlong }
      : undefined,
  };
}

function watchingWords(services: readonly ServiceView[], zone: Zone, language: Language): Words {
  const t = MESSAGES[language];
  const tag = <HealthBadge health="degraded" label={t.health.watching} />;
  if (services.length > 1) {
    return {
      tag,
      headline: t.overview.watchingMany(watchedNames(services, t)),
      body: t.overview.lookIfMany,
    };
  }
  const [service] = services;
  const when = (iso: string) => formatTime(iso, zone, language);
  const { reason, look, detail } = watchReason(service, t, when);
  return {
    tag,
    headline: t.overview.watchingOne(service.name, reason),
    body: look,
    detail: detail && showInvisible(detail),
    link: { href: serviceHref(service.id), label: t.overview.showService },
  };
}

/** Why Bleep is not looking into a service shown down, as a sentence; the incident it stayed
 * down through is named and linked. */
function NotLookingLine({ service }: { service: ServiceView }) {
  const t = useT().overview;
  const unwatched = service.unwatched;
  const ended = unwatched?.reason === "after_incident" ? unwatched.incident_id : null;
  if (!unwatched || !ended) {
    const reason =
      !unwatched || unwatched.reason === "after_incident" ? "unknown" : unwatched.reason;
    return <>{t.notLooking[reason]}</>;
  }
  const [before, after] = t.stillDownAfter;
  return (
    <>
      {before}
      <Link
        href={`/incidents/${ended}`}
        className="font-bold text-accent underline-offset-4 hover:underline"
      >
        {unwatched.incident_short_name ? (
          <ShortName name={unwatched.incident_short_name} className="text-accent" />
        ) : (
          t.itsLastIncident
        )}
      </Link>
      {after}
    </>
  );
}

function unwatchedWords(services: readonly ServiceView[], language: Language, ask: Ask): Words {
  const t = MESSAGES[language];
  const tag = <HealthBadge health="degraded" label={t.health.yourCall} />;
  const [first] = services;
  const action = { label: t.overview.askToLook, onClick: () => ask(t.overview.lookAt(first.name)) };
  if (services.length > 1) {
    return {
      tag,
      headline: t.overview.unwatchedMany(watchedNames(services, t)),
      body: null,
      action,
    };
  }
  const detail = unwatchedDetail(first);
  return {
    tag,
    headline: t.overview.unwatchedOne(first.name),
    body: <NotLookingLine service={first} />,
    detail: detail && showInvisible(detail),
    action,
  };
}

function wordsFor(
  situation: Situation,
  hosts: readonly HostView[],
  services: readonly ServiceView[],
  zone: Zone,
  language: Language,
  ask: Ask,
): Words {
  const t = MESSAGES[language];
  switch (situation.kind) {
    case "incident":
      return incidentWords(situation, hosts, services, t);
    case "unreachable":
      return unreachableWords(situation, services, zone, language);
    case "watching":
      return watchingWords(situation.services, zone, language);
    case "unwatched":
      return unwatchedWords(situation.services, language, ask);
    case "quiet":
      return {
        tag: <HealthBadge health="ok" label={t.health.allQuiet} />,
        headline: t.overview.nothingNeeds,
        body: t.overview.watching(t.counts.hosts(hosts.length), t.counts.services(services.length)),
      };
    case "no_hosts":
      return {
        tag: <HealthBadge health="unknown" label={t.health.noHosts} />,
        headline: t.overview.noHostsToWatch,
        body: null,
      };
  }
}

/**
 * The first thing on the overview: Bleep in the overall mood, a status tag, and a headline that
 * answers what failed and whether the human is needed, with one line and a link under it.
 */
export function OverviewHero({
  situation,
  mood,
  hosts,
  services,
}: {
  situation: Situation;
  mood: Mood;
  hosts: readonly HostView[];
  services: readonly ServiceView[];
}) {
  const zone = useTimeZone();
  const language = useLanguage();
  const t = useT();
  const ask = useAskBleep();
  const words = wordsFor(situation, hosts, services, zone, language, ask);
  const others = situation.kind === "incident" ? situation.others : 0;
  return (
    <section
      aria-labelledby="overview-headline"
      className="flex items-start gap-4 rounded-card-sm border border-line bg-surface p-4 sm:items-center sm:gap-8 sm:p-7 md:rounded-card md:px-8"
    >
      <Bleep mood={mood} size={132} className="size-[76px] sm:size-[132px]" />
      <div className="flex min-w-0 flex-1 flex-col items-start gap-3">
        {words.tag}
        <h1
          id="overview-headline"
          className="text-[22px] leading-tight font-extrabold tracking-tight wrap-anywhere hyphens-auto sm:text-[34px]"
        >
          {words.headline}
        </h1>
        {words.body && (
          <p className="max-w-[600px] text-[15px] leading-normal text-ink-2 sm:text-[17px]">
            {words.body}
          </p>
        )}
        {words.detail && (
          <p className="font-mono text-[13px] break-words text-ink-2">{words.detail}</p>
        )}
        {(words.link || words.action || others > 0) && (
          <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-2">
            {words.link && (
              <Link
                href={words.link.href}
                className="inline-flex min-h-12 items-center gap-2 rounded-full bg-accent px-5 text-base font-extrabold text-accent-ink transition-[filter] hover:brightness-110 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-surface focus-visible:outline-none"
              >
                {words.link.label}
                <ArrowRight aria-hidden className="size-[18px]" />
              </Link>
            )}
            {words.action && (
              <button
                type="button"
                onClick={words.action.onClick}
                className="inline-flex min-h-12 items-center gap-2 rounded-full bg-accent px-5 text-base font-extrabold text-accent-ink transition-[filter] hover:brightness-110 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-surface focus-visible:outline-none"
              >
                <MessageCircle aria-hidden className="size-[18px]" />
                {words.action.label}
              </button>
            )}
            {others > 0 && (
              <Link
                href="/incidents"
                className="inline-flex min-h-11 items-center rounded-full font-bold text-accent underline-offset-4 hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
              >
                {t.overview.moreNeed(others)}
              </Link>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
