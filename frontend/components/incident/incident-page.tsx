"use client";

import { ArrowLeft, Loader2, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { DiagnosisCard } from "@/components/incident/diagnosis-card";
import { subjectText } from "@/components/incident/incident-list";
import { ProposalCard } from "@/components/incident/proposal-card";
import { ReasoningStream } from "@/components/incident/reasoning-stream";
import { ReportView } from "@/components/incident/report-view";
import { IncidentStatusBadge } from "@/components/incident/status-badge";
import { Timeline } from "@/components/incident/timeline";
import { Badge } from "@/components/ui/badge";
import { ApiError, apiFetch } from "@/lib/api";
import type { IncidentDetail, IncidentEvent } from "@/lib/api-types";
import { formatDateTime } from "@/lib/format";
import { type StreamStatus, useIncidentStream } from "@/lib/use-incident-stream";
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

const STREAM: Record<StreamStatus, { label: string; dot: string }> = {
  loading: { label: "Connecting", dot: "bg-health-unknown" },
  live: { label: "Live", dot: "bg-health-ok" },
  reconnecting: { label: "Reconnecting", dot: "bg-health-degraded" },
};

function StreamBadge({ status }: { status: StreamStatus }) {
  const { label, dot } = STREAM[status];
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
      <span aria-hidden className={cn("size-2 rounded-full", dot)} />
      {label}
    </span>
  );
}

function Panel({
  title,
  children,
  className,
}: {
  title: string;
  children: React.ReactNode;
  className?: string;
}) {
  const id = `panel-${title.toLowerCase().replaceAll(" ", "-")}`;
  return (
    <section
      aria-labelledby={id}
      className={cn("min-w-0 rounded-lg border border-border bg-card", className)}
    >
      <h2 id={id} className="px-4 py-3 text-sm font-semibold">
        {title}
      </h2>
      <div className="border-t border-border px-4 py-3">{children}</div>
    </section>
  );
}

function Waiting({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-muted-foreground">{children}</p>;
}

export function IncidentPage({ id }: { id: string }) {
  const { events, status } = useIncidentStream(id);
  const [reloads, setReloads] = useState(0);
  const { data, error } = useIncidentDetail(id, `${reloadKey(events)}:${reloads}`);

  const back = (
    <Link
      href="/incidents"
      className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
    >
      <ArrowLeft aria-hidden className="size-4" />
      Incidents
    </Link>
  );

  if (!data) {
    return (
      <div className="flex flex-col gap-4">
        {back}
        {error ? (
          <p role="alert" className="flex items-center gap-2 text-sm text-health-down">
            <TriangleAlert aria-hidden className="size-4" />
            {error instanceof ApiError && error.status === 404
              ? "There is no incident with this id."
              : `Could not load the incident: ${error.message}`}
          </p>
        ) : (
          <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 aria-hidden className="size-4 animate-spin" />
            Loading the incident
          </p>
        )}
      </div>
    );
  }

  const { incident, diagnosis, proposal, approval, outcome, report } = data;
  return (
    <div className="flex flex-col gap-4">
      {back}
      <header className="flex flex-col gap-1.5">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <h1 className="text-lg font-semibold">{incident.title}</h1>
          <IncidentStatusBadge status={incident.status} />
          {outcome && <Badge>{outcome.replaceAll("_", " ")}</Badge>}
          <span className="ml-auto">
            <StreamBadge status={status} />
          </span>
        </div>
        <p className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span className="font-mono">{subjectText(incident)}</span>
          <span>
            Opened <time dateTime={incident.opened_at}>{formatDateTime(incident.opened_at)}</time>
          </span>
          {incident.tokens > 0 && <span>{incident.tokens.toLocaleString("en-US")} tokens</span>}
          {incident.cost_usd != null && <span>${incident.cost_usd.toFixed(3)}</span>}
        </p>
        {error && (
          <p role="alert" className="flex items-center gap-2 text-xs text-health-degraded">
            <TriangleAlert aria-hidden className="size-3.5" />
            Refresh failed, showing the last data: {error.message}
          </p>
        )}
      </header>

      {report && (
        <Panel title="Report">
          <ReportView report={report} outcome={outcome} />
        </Panel>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Diagnosis">
          {diagnosis ? (
            <DiagnosisCard diagnosis={diagnosis} />
          ) : (
            <Waiting>No diagnosis yet.</Waiting>
          )}
        </Panel>
        <Panel title="Proposal">
          {proposal || approval ? (
            <ProposalCard
              incidentId={incident.id}
              proposal={proposal}
              approval={approval}
              onDecided={() => setReloads((count) => count + 1)}
            />
          ) : (
            <Waiting>No plan yet.</Waiting>
          )}
        </Panel>
      </div>

      <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
        <Panel title="Live reasoning">
          <ReasoningStream events={events} />
        </Panel>
        <Panel title="Timeline">
          <Timeline events={events} />
        </Panel>
      </div>
    </div>
  );
}
