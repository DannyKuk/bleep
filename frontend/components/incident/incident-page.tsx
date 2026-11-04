"use client";

import { ArrowLeft, ChevronRight, Loader2, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { DiagnosisCard } from "@/components/incident/diagnosis-card";
import { FeedbackBar } from "@/components/incident/feedback";
import { IncidentHero, PhoneIncidentHero } from "@/components/incident/incident-hero";
import { planRun } from "@/components/incident/plan-run";
import { progressSteps } from "@/components/incident/progress";
import { ProgressBar } from "@/components/incident/progress-bar";
import { barApproval, planTitle, ProposalCard } from "@/components/incident/proposal-card";
import { ReasoningStream, toolSteps } from "@/components/incident/reasoning-stream";
import { ReportView } from "@/components/incident/report-view";
import { Timeline } from "@/components/incident/timeline";
import { usePhone } from "@/components/incident/use-phone";
import { Panel } from "@/components/panel";
import { ApiError, apiFetch } from "@/lib/api";
import type {
  FeedbackTarget,
  IncidentDetail,
  IncidentEvent,
  IncidentStatus,
} from "@/lib/api-types";
import { useIncidentStream } from "@/lib/use-incident-stream";
import { cn } from "@/lib/utils";

// Events that never change the incident's findings; every other kind reloads the detail.
const NO_RELOAD = new Set(["tool_call", "tool_result"]);
// The runner sets the incident's status after the run's last event (awaiting approval, resolved),
// so the detail is also read again on this interval while the tab is visible.
export const DETAIL_POLL_MS = 10_000;

function reloadKey(events: IncidentEvent[]): number {
  return events.findLast((event) => !NO_RELOAD.has(event.kind))?.seq ?? 0;
}

interface Loaded {
  data?: IncidentDetail;
  error?: Error;
}

/**
 * The incident's findings, read again whenever `version` changes and every `DETAIL_POLL_MS` while
 * the tab is visible; the newest request wins.
 */
function useIncidentDetail(id: string, version: string): Loaded {
  const [state, setState] = useState<Loaded>({});
  const [ticks, setTicks] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => {
      if (document.visibilityState !== "hidden") setTicks((count) => count + 1);
    }, DETAIL_POLL_MS);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    apiFetch<IncidentDetail>(`/api/incidents/${encodeURIComponent(id)}`, {
      signal: controller.signal,
    })
      .then((data) => setState({ data }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        const failure = error instanceof Error ? error : new Error(String(error));
        setState((prev) => ({ data: prev.data, error: failure }));
      });
    return () => controller.abort();
  }, [id, version, ticks]);
  return state;
}

const ENDED = new Set<IncidentStatus>(["resolved", "closed", "failed"]);

function Waiting({ children }: { children: React.ReactNode }) {
  return <p className="text-[15px] text-ink-2">{children}</p>;
}

export function IncidentPage({ id }: { id: string }) {
  const { events, status } = useIncidentStream(id);
  const phone = usePhone();
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
          Incidents
        </Link>
        {error ? (
          <p role="alert" className="flex items-center gap-2 text-sm font-bold text-down">
            <TriangleAlert aria-hidden className="size-4" />
            {error instanceof ApiError && error.status === 404
              ? "There is no incident with this id."
              : `Could not load the incident: ${error.message}`}
          </p>
        ) : (
          <p role="status" className="flex items-center gap-2 text-sm text-ink-2">
            <Loader2 aria-hidden className="size-4 motion-safe:animate-spin" />
            Loading the incident
          </p>
        )}
      </div>
    );
  }

  const { incident, diagnosis, proposal, approval, report, feedback } = data;
  const reload = () => setReloads((count) => count + 1);
  const vote = (target: FeedbackTarget) => feedback.find((given) => given.target === target);
  const ended = ENDED.has(incident.status);
  // Known only once the history has loaded and the stream is open; until then a no says only that
  // this plan did not run, which is true either way.
  const nothingRanBefore =
    status === "live" &&
    !events.some(({ kind, payload }) => kind === "action" && payload.status !== "skipped");

  const refreshFailed = error && (
    <p role="alert" className="flex items-center gap-2 text-sm font-bold text-warn">
      <TriangleAlert aria-hidden className="size-4" />
      Refresh failed, showing the last data: {error.message}
    </p>
  );
  const reportPanel = report && (
    <Panel title="Report">
      <ReportView report={report} outcome={data.outcome} />
    </Panel>
  );
  const planPanel = (
    <Panel title={planTitle(data)} awaitingYou={approval?.status === "pending"}>
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
        <Waiting>{ended ? "I made no plan." : "I have no plan yet."}</Waiting>
      )}
    </Panel>
  );
  const foundPanel = (
    <Panel title="What I found">
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
        <Waiting>{ended ? "I found no cause." : "I am still looking."}</Waiting>
      )}
    </Panel>
  );

  // One tree for both layouts, with the plan card at the same place in it: crossing the
  // breakpoint (a tablet turned) moves the decision bar instead of mounting a new one, so an
  // answer in flight, the lock after it and a typed instruction all survive.
  const looks = toolSteps(events).length;
  return (
    <div
      className={cn(
        "flex flex-col",
        phone ? "gap-3.5" : "gap-6",
        // Room under the last card for the decision bar pinned over the bottom of the screen.
        phone && barApproval(proposal, approval) && "pb-52",
      )}
    >
      {phone ? <PhoneIncidentHero detail={data} /> : <IncidentHero detail={data} stream={status} />}
      {refreshFailed}
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
          {phone && foundPanel}
          {planPanel}
          {!phone && foundPanel}
          {phone ? (
            <Folded title="How I got there" note={looks === 1 ? "1 look" : `${looks} looks`}>
              <ReasoningStream events={events} />
            </Folded>
          ) : (
            <Panel title="How I got there">
              <ReasoningStream events={events} />
            </Panel>
          )}
        </div>
        {phone ? (
          <Folded title="Timeline">
            <Timeline events={events} incident={incident} />
          </Folded>
        ) : (
          <Panel title="Timeline" className="sticky top-6">
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
