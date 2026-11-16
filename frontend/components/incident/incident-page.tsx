"use client";

import { ArrowLeft, ChevronRight, Loader2, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useCallback, useState } from "react";

import { DiagnosisCard } from "@/components/incident/diagnosis-card";
import { ExpectedButton } from "@/components/incident/expected-button";
import { FeedbackBar } from "@/components/incident/feedback";
import { IncidentHero, PhoneIncidentHero } from "@/components/incident/incident-hero";
import { planRun } from "@/components/incident/plan-run";
import { progressSteps } from "@/components/incident/progress";
import { ProgressBar } from "@/components/incident/progress-bar";
import {
  awaitsAnswer,
  barApproval,
  ENDED,
  planTitle,
  ProposalCard,
} from "@/components/incident/proposal-card";
import { ReasoningStream, toolSteps } from "@/components/incident/reasoning-stream";
import { Repeats } from "@/components/incident/repeats";
import { ReportView } from "@/components/incident/report-view";
import { Timeline } from "@/components/incident/timeline";
import { usePhone } from "@/hooks/use-phone";
import { useOverall } from "@/components/overall-state";
import { Panel } from "@/components/panel";
import { QuietMenu } from "@/components/quiet/quiet-menu";
import { Button } from "@/components/ui/button";
import { ApiError, apiFetch } from "@/lib/api";
import type {
  FeedbackTarget,
  IncidentDetail,
  IncidentEvent,
  IncidentStatus,
} from "@/lib/api-types";
import { useT } from "@/lib/i18n";
import { useIncidentStream } from "@/hooks/use-incident-stream";
import { POLL_MS, type Poll, usePoll } from "@/hooks/use-poll";
import { cn } from "@/lib/utils";

// Events that never change the incident's findings; every other kind reloads the detail.
const NO_RELOAD = new Set(["tool_call", "tool_result"]);

function reloadKey(events: IncidentEvent[]): number {
  return events.findLast((event) => !NO_RELOAD.has(event.kind))?.seq ?? 0;
}

/**
 * The incident's findings, read again whenever `version` changes and on every poll while the tab
 * is visible (the runner sets the incident's status after the run's last event, so no event
 * announces it); the newest request wins.
 */
function useIncidentDetail(id: string, version: string): Poll<IncidentDetail> {
  const fetchDetail = useCallback(
    (signal: AbortSignal) =>
      apiFetch<IncidentDetail>(`/api/incidents/${encodeURIComponent(id)}`, { signal }),
    // `version` is no input of the fetch: a new one makes a new fetcher, which polls at once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [id, version],
  );
  return usePoll(fetchDetail, POLL_MS);
}

// A quiet choice has nothing left to silence once the incident ended, and would cut short a run
// that is changing the host while it executes or verifies.
const NOT_QUIETABLE = new Set<IncidentStatus>([...ENDED, "executing", "verifying"]);

function Waiting({ children }: { children: React.ReactNode }) {
  return <p className="text-[15px] text-ink-2">{children}</p>;
}

export function IncidentPage({ id }: { id: string }) {
  const { events, status } = useIncidentStream(id);
  const overall = useOverall();
  const phone = usePhone();
  const t = useT();
  const words = t.incidents;
  const [reloads, setReloads] = useState(0);
  const { data, error } = useIncidentDetail(id, `${reloadKey(events)}:${reloads}`);

  if (!data) {
    return (
      <div className="flex flex-col gap-4">
        <Link
          href="/incidents"
          className="inline-flex w-fit items-center gap-1 rounded-full text-sm font-bold text-ink-2 hover:text-ink focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          <ArrowLeft aria-hidden className="size-4" />
          {words.title}
        </Link>
        {error ? (
          <p role="alert" className="flex items-center gap-2 text-sm font-bold text-down">
            <TriangleAlert aria-hidden className="size-4" />
            {error instanceof ApiError && error.status === 404
              ? words.notFound
              : words.loadFailed(error.message)}
          </p>
        ) : (
          <p role="status" className="flex items-center gap-2 text-sm text-ink-2">
            <Loader2 aria-hidden className="size-4 motion-safe:animate-spin" />
            {words.loading}
          </p>
        )}
      </div>
    );
  }

  const { incident, diagnosis, proposal, approval, report, feedback } = data;
  const reload = () => setReloads((count) => count + 1);
  const quietChanged = () => {
    reload();
    overall.refresh();
  };
  const vote = (target: FeedbackTarget) => feedback.find((given) => given.target === target);
  const ended = ENDED.has(incident.status);
  // Known only once the history has loaded and the stream is open; until then a no says only that
  // this plan did not run, which is true either way.
  const nothingRanBefore =
    status === "live" &&
    !events.some(({ kind, payload }) => kind === "action" && payload.status !== "skipped");

  const { subject } = incident;
  const hostName = overall.hosts?.find((host) => host.id === subject.host_id)?.name;
  const quietTarget =
    subject.kind === "service"
      ? subject.service_id
        ? ({
            scope: "service",
            id: subject.service_id,
            name: subject.service_id.split(":").pop() ?? subject.service_id,
          } as const)
        : null
      : ({ scope: "host", id: subject.host_id, name: hostName ?? subject.host_id } as const);
  const quietable = quietTarget && !NOT_QUIETABLE.has(incident.status);
  const imOnIt = (quietable || data.expectation) && (
    <div className="flex min-w-0 flex-wrap items-start justify-end gap-2">
      {data.expectation && (
        <ExpectedButton
          incidentId={incident.id}
          expectation={data.expectation}
          onDone={quietChanged}
        />
      )}
      {quietable && (
        <QuietMenu
          target={quietTarget}
          quiet={null}
          onChanged={quietChanged}
          trigger={
            <Button variant="outline" aria-label={words.onIt}>
              {words.onItMenu}
            </Button>
          }
        />
      )}
    </div>
  );

  const refreshFailed = error && (
    <p role="alert" className="flex items-center gap-2 text-sm font-bold text-warn">
      <TriangleAlert aria-hidden className="size-4" />
      {t.common.refreshFailed(error.message)}
    </p>
  );
  // The report is its own document card, so it sits on the page without a panel around it.
  const reportPanel = report && (
    <ReportView report={report} outcome={data.outcome} incident={incident} />
  );
  const planPanel = (
    <Panel
      key="plan"
      title={planTitle(data, t)}
      awaitingYou={awaitsAnswer(approval, incident.status)}
    >
      {proposal || approval ? (
        <div className="flex flex-col gap-4">
          <ProposalCard
            incidentId={incident.id}
            proposal={proposal}
            approval={approval}
            onDecided={reload}
            run={planRun(events)}
            status={incident.status}
            pinned={phone}
            nothingRanBefore={nothingRanBefore}
          />
          {proposal && (
            <FeedbackBar
              incidentId={incident.id}
              target="proposal"
              current={vote("proposal")}
              onSaved={reload}
            />
          )}
        </div>
      ) : (
        <Waiting>{ended ? words.madeNoPlan : words.noPlanYet}</Waiting>
      )}
    </Panel>
  );
  const foundPanel = (
    <Panel key="found" title={words.found}>
      {diagnosis ? (
        <div className="flex flex-col gap-4">
          <DiagnosisCard diagnosis={diagnosis} short={phone} />
          <FeedbackBar
            incidentId={incident.id}
            target="diagnosis"
            current={vote("diagnosis")}
            onSaved={reload}
          />
        </div>
      ) : (
        <Waiting>{ended ? words.foundNoCause : words.stillLooking}</Waiting>
      )}
    </Panel>
  );

  // One tree for both layouts: crossing the breakpoint (a tablet turned) moves the decision bar
  // and the feedback drafts instead of mounting new ones, so an answer in flight, the lock after
  // it, a typed instruction and a typed note all survive. The two panels swap places as keyed
  // siblings, which React moves rather than remounts.
  const looks = toolSteps(events).length;
  return (
    <div
      className={cn(
        "flex flex-col",
        phone ? "gap-3.5" : "gap-6",
        // Room under the last card for the decision bar pinned over the bottom of the screen.
        phone && barApproval(proposal, approval, incident.status) && "pb-52",
      )}
    >
      {phone ? <PhoneIncidentHero detail={data} /> : <IncidentHero detail={data} stream={status} />}
      {refreshFailed}
      <Repeats incident={incident} repeats={data.repeats} />
      {imOnIt}
      {!phone && <ProgressBar steps={progressSteps(events, incident.status)} />}
      <div
        className={cn(
          phone
            ? "flex flex-col gap-3.5"
            : "grid grid-cols-[minmax(0,1fr)_320px] items-start gap-5",
        )}
      >
        <div className={cn("flex min-w-0 flex-col", phone ? "gap-3.5" : "gap-5")}>
          {reportPanel}
          {phone ? [foundPanel, planPanel] : [planPanel, foundPanel]}
          {phone ? (
            <Folded title={words.howIGotThere} note={words.looks(looks)}>
              <ReasoningStream events={events} />
            </Folded>
          ) : (
            <Panel title={words.howIGotThere}>
              <ReasoningStream events={events} />
            </Panel>
          )}
        </div>
        {phone ? (
          <Folded title={words.timeline}>
            <Timeline events={events} incident={incident} />
          </Folded>
        ) : (
          <Panel title={words.timeline} className="sticky top-6">
            <Timeline events={events} incident={incident} />
          </Panel>
        )}
      </div>
    </div>
  );
}

/** A card folded to its title on the phone, so nothing competes with the plan. */
function Folded({
  title,
  note,
  children,
}: {
  title: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <details className="group rounded-card-sm border border-line bg-surface">
      <summary className="flex min-h-[52px] cursor-pointer list-none items-center gap-2 rounded-card-sm px-4 text-[15px] font-extrabold focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none [&::-webkit-details-marker]:hidden">
        <ChevronRight
          aria-hidden
          className="size-4 text-ink-2 transition-transform group-open:rotate-90 motion-reduce:transition-none"
        />
        {title}
        {note && <span className="ml-auto text-[13px] font-semibold text-ink-2">{note}</span>}
      </summary>
      <div className="px-4 pb-4">{children}</div>
    </details>
  );
}
