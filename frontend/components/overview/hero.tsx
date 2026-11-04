import { ArrowRight } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { Bleep } from "@/components/bleep/bleep";
import { IncidentStatusBadge } from "@/components/incident/status-badge";
import { HealthBadge } from "@/components/overview/health-badge";
import { joinNames, type Situation } from "@/components/overview/situation";
import type {
  Action,
  Health,
  HostView,
  Incident,
  IncidentDetail,
  PlanStep,
  ServiceView,
} from "@/lib/api-types";
import { formatDateTime, showInvisible } from "@/lib/format";
import type { Mood } from "@/lib/mood";

const TROUBLE: Record<Health, string> = {
  down: "is down",
  degraded: "is struggling",
  stopped: "has stopped",
  unknown: "has a problem",
  ok: "had a problem",
};

const VERB: Record<Action, string> = {
  "docker.restart": "restart",
  "docker.stop": "stop",
  "docker.start": "start",
  "coolify.redeploy": "redeploy",
  "coolify.set_env": "change a setting of",
  "mcp.call": "call",
};

const PLAN_STEPS_SHOWN = 2;

interface Words {
  tag: ReactNode;
  headline: string;
  body: ReactNode;
  /** Monospace detail under the body, such as a host's error. */
  detail?: string;
  link?: { href: string; label: string };
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

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
  const shown = steps.slice(0, PLAN_STEPS_SHOWN);
  const more = steps.length - shown.length;
  return (
    <>
      I want to{" "}
      {shown.map((step, index) => (
        <span key={index}>
          {index > 0 && ", then "}
          {VERB[step.action]} <code className="font-mono">{showInvisible(step.target)}</code>
        </span>
      ))}
      {more > 0 && `, and ${plural(more, "more step")}`}. I will not touch anything until you say
      yes.
    </>
  );
}

/** The plan put to the human: the pending approval's, else the run's latest. */
function planOf(detail: IncidentDetail | undefined) {
  if (!detail) return null;
  return detail.approval?.status === "pending" ? detail.approval.proposal : detail.proposal;
}

function waitingWords(incident: Incident, detail: IncidentDetail | undefined, trouble: string) {
  const plan = planOf(detail);
  const unsure = detail?.diagnosis?.unsure === true;
  let reason = "I need you.";
  let body: ReactNode = incident.title;
  if (plan?.refused) {
    reason = "My own checks stopped my plan, so I need you.";
  } else if (plan?.kind === "no_action") {
    reason = unsure
      ? "I am not sure why, and I have no fix to run."
      : "I know why, but I have no fix to run.";
  } else if (plan) {
    reason = unsure ? "I am not sure why, but I have a plan." : "I know why, and I have a fix.";
    body = <PlanLine steps={plan.steps} />;
  }
  return { headline: `${trouble} ${reason}`, body };
}

function incidentWords(
  situation: Extract<Situation, { kind: "incident" }>,
  hosts: readonly HostView[],
  services: readonly ServiceView[],
): Words {
  const { incident, detail } = situation;
  const subject = subjectOf(incident, hosts, services);
  const trouble = `${subject.name} ${TROUBLE[subject.health ?? "unknown"]}.`;
  const href = `/incidents/${incident.id}`;
  const tag = <IncidentStatusBadge status={incident.status} />;
  if (incident.status === "awaiting_approval") {
    return {
      tag,
      ...waitingWords(incident, detail, trouble),
      link: { href, label: "Show me the plan" },
    };
  }
  if (incident.status === "failed") {
    return {
      tag,
      headline: `I could not finish with ${subject.name}.`,
      body: incident.title,
      link: { href, label: "Show me what happened" },
    };
  }
  return {
    tag,
    headline: `${trouble} I am on it.`,
    body: incident.title,
    link: { href, label: "Follow along" },
  };
}

function unreachableWords(
  situation: Extract<Situation, { kind: "unreachable" }>,
  services: readonly ServiceView[],
): Words {
  const { host, incident } = situation;
  const names = services.filter((service) => service.host_id === host.id).map((s) => s.name);
  const since = host.last_seen
    ? `I last heard from it at ${formatDateTime(host.last_seen)}`
    : "I have not reached it yet";
  const blind = names.length > 0 ? `, so I cannot see ${joinNames(names)}` : "";
  return {
    tag: <HealthBadge health="down" label="Unreachable" />,
    headline: `I cannot reach ${host.name}.`,
    body: `${since}${blind}.`,
    detail: host.error ?? undefined,
    link: incident ? { href: `/incidents/${incident.id}`, label: "Follow along" } : undefined,
  };
}

function wordsFor(
  situation: Situation,
  hosts: readonly HostView[],
  services: readonly ServiceView[],
): Words {
  switch (situation.kind) {
    case "incident":
      return incidentWords(situation, hosts, services);
    case "unreachable":
      return unreachableWords(situation, services);
    case "quiet":
      return {
        tag: <HealthBadge health="ok" label="All quiet" />,
        headline: "Nothing needs you right now.",
        body: `I am watching ${plural(hosts.length, "host")} and ${plural(services.length, "service")}.`,
      };
    case "no_hosts":
      return {
        tag: <HealthBadge health="unknown" label="No hosts" />,
        headline: "I have no hosts to watch yet.",
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
  const words = wordsFor(situation, hosts, services);
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
          className="text-[22px] leading-tight font-extrabold tracking-tight sm:text-[34px]"
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
        {(words.link || others > 0) && (
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
            {others > 0 && (
              <Link
                href="/incidents"
                className="inline-flex min-h-11 items-center rounded-full font-bold text-accent underline-offset-4 hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
              >
                {plural(others, "more incident")} {others === 1 ? "needs" : "need"} you
              </Link>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
