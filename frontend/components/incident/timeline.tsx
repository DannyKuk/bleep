import { CircleAlert, X } from "lucide-react";

import { LOOK, type Mark } from "@/components/incident/colour-code";
import { RunSoFar } from "@/components/incident/progress";
import { useTimeZone } from "@/components/time-zone";
import type { Incident, IncidentEvent } from "@/lib/api-types";
import { CHANNEL, formatTime } from "@/lib/format";
import { cn } from "@/lib/utils";

// Each node in two tenses: while it runs and once it is done.
const NODES: Record<string, [string, string]> = {
  gather: ["Gathering context", "Gathered context"],
  investigate: ["Investigating", "Investigated"],
  diagnose: ["Diagnosing", "Diagnosed"],
  propose: ["Planning a fix", "Planned"],
  approval: ["Waiting for your answer", "Answered"],
  execute: ["Running the plan", "Ran the plan"],
  verify: ["Checking the service", "Checked the service"],
  report: ["Writing the report", "Wrote the report"],
};

/** A line is running while its mark is `current`. */
interface Entry {
  seq: number;
  at: string;
  mark: Mark;
  text: string;
}

const ACTION_MARK: Record<string, Mark> = { done: "done", failed: "failed", skipped: "todo" };
const DECISION_MARK: Record<string, Mark> = { approve: "done", reject: "declined" };

function channel(payload: Record<string, unknown>): string {
  const via = payload.via;
  return via === "telegram" || via === "dashboard" ? CHANNEL[via] : "a channel";
}

function decisionText(payload: Record<string, unknown>): string {
  const where = channel(payload);
  if (payload.kind === "approve") return `Approved on ${where}`;
  if (payload.kind === "reject") {
    return payload.refused === true ? `Let go on ${where}` : `Rejected on ${where}`;
  }
  return `Instruction from ${where}: “${String(payload.text ?? "")}”`;
}

/**
 * The incident's steps, one line each, marked in the incident page's colour code; a node's start
 * and finish become one line, which ends as the progress bar's step does (`RunSoFar`).
 */
export function timelineEntries(events: IncidentEvent[]): Entry[] {
  const entries: Entry[] = [];
  const open = new Map<string, Entry>();
  const openSteps = new Map<string, Entry>();
  const run = new RunSoFar();
  for (const event of events) {
    run.follow(event);
    const { kind, payload, seq, at } = event;
    const node = String(payload.node ?? "");
    const [running, done] = NODES[node] ?? [`Running ${node}`, `Finished ${node}`];
    switch (kind) {
      case "node_started": {
        const entry: Entry = { seq, at, mark: "current", text: running };
        // A node that stopped at an interrupt starts again on resume without having finished, so
        // its earlier line is no longer running.
        const interrupted = open.get(node);
        if (interrupted) interrupted.mark = "info";
        open.set(node, entry);
        entries.push(entry);
        break;
      }
      case "node_finished": {
        const mark = run.finished(node);
        const entry = open.get(node);
        if (entry) {
          Object.assign(entry, { mark, text: done });
          open.delete(node);
        } else {
          entries.push({ seq, at, mark, text: done });
        }
        break;
      }
      case "tool_call":
      case "tool_result":
        break; // the reasoning stream shows these
      case "report":
        break; // the report panel shows it, and the node's line says it was written
      case "proposal":
        entries.push({
          seq,
          at,
          mark: "info",
          text: payload.kind === "no_action" ? "Proposed no action" : "Proposed a plan",
        });
        break;
      case "approval_requested":
        entries.push({ seq, at, mark: "info", text: "Asked for approval" });
        break;
      case "decision": {
        // The wait ends with the answer, before Approval resumes and finishes.
        const waiting = open.get("approval");
        if (waiting) waiting.mark = "done";
        open.delete("approval");
        entries.push({
          seq,
          at,
          mark: DECISION_MARK[String(payload.kind)] ?? "info",
          text: decisionText(payload),
        });
        break;
      }
      case "action": {
        // Execute, one per step status change: a started step runs until its next status.
        const step = `${String(payload.action ?? "")} ${String(payload.target ?? "")}`;
        const status = String(payload.status ?? "");
        const started = openSteps.get(step);
        if (started) started.mark = "info";
        openSteps.delete(step);
        const entry: Entry = {
          seq,
          at,
          mark: status === "started" ? "current" : (ACTION_MARK[status] ?? "info"),
          text: `${step}: ${status}`,
        };
        if (status === "started") openSteps.set(step, entry);
        entries.push(entry);
        break;
      }
      case "verification":
        entries.push({
          seq,
          at,
          mark: payload.ok ? "done" : "warning",
          text: `Check ${String(payload.attempt ?? 1)} ${payload.ok ? "passed" : "failed"}`,
        });
        break;
      case "expected":
        entries.push({ seq, at, mark: "info", text: String(payload.text ?? "") });
        break;
      case "notice":
        entries.push({ seq, at, mark: "warning", text: String(payload.text ?? "") });
        break;
      default:
        entries.push({ seq, at, mark: "info", text: kind.replaceAll("_", " ") });
    }
  }
  return entries;
}

const END: Partial<Record<Incident["status"], { text: string; mark: Mark }>> = {
  resolved: { text: "Resolved", mark: "done" },
  closed: { text: "Closed", mark: "info" },
  failed: { text: "Failed", mark: "failed" },
};

/** The incident's own start and end around its events: what opened it, and how it ended. */
function withEnds(entries: Entry[], incident: Incident): Entry[] {
  const opened: Entry = { seq: -1, at: incident.opened_at, mark: "opened", text: incident.title };
  const end = END[incident.status];
  if (end) {
    // An ended incident has nothing running: a failed run stopped at what still ran, and
    // anything else never finished it, as the progress bar shows.
    for (const entry of entries.filter(({ mark }) => mark === "current")) {
      entry.mark = incident.status === "failed" ? "stopped" : "todo";
    }
  }
  const closed: Entry[] =
    end && incident.closed_at
      ? [{ seq: Number.MAX_SAFE_INTEGER, at: incident.closed_at, ...end }]
      : [];
  return [opened, ...entries, ...closed];
}

/** The line's dot, a cross for a line that went wrong or a no, or an alert for what broke. */
function Marker({ mark }: { mark: Mark }) {
  const look = LOOK[mark];
  const icon = {
    cross: <X className={cn("size-3.5", look.ink)} strokeWidth={3} />,
    alert: <CircleAlert className={cn("size-3.5", look.ink)} strokeWidth={2.5} />,
    dot: (
      <span
        className={cn(
          "size-2.5 rounded-full",
          look.fill,
          mark === "current" && "motion-safe:animate-pulse",
        )}
      />
    ),
  }[look.icon];
  return (
    <span aria-hidden className="mt-[3px] flex size-3.5 shrink-0 items-center justify-center">
      {icon}
    </span>
  );
}

/**
 * What happened to the incident, in order: steps, the question, the answer, notices. Each line
 * is marked in the colour code the progress bar uses, and says its state to a screen reader, so
 * the colour is never the only cue. With the incident, the first line is what opened it and the
 * last how it ended.
 */
export function Timeline({ events, incident }: { events: IncidentEvent[]; incident?: Incident }) {
  const steps = timelineEntries(events);
  const zone = useTimeZone();
  if (steps.length === 0 && !incident) {
    return <p className="text-sm text-ink-2">Nothing has happened yet.</p>;
  }
  const entries = incident ? withEnds(steps, incident) : steps;
  return (
    <ol aria-label="Timeline" className="flex flex-col gap-3">
      {entries.map(({ seq, at, mark, text }) => (
        <li key={seq} data-state={mark} className="flex items-start gap-2.5 text-sm">
          <Marker mark={mark} />
          <span
            className={cn(
              "min-w-0 flex-1 break-words",
              mark === "current" && "font-extrabold",
              LOOK[mark].tone,
            )}
          >
            {text}
          </span>
          <span className="sr-only">, {LOOK[mark].word}</span>
          <time dateTime={at} className="shrink-0 font-mono text-xs text-ink-2">
            {formatTime(at, zone)}
          </time>
        </li>
      ))}
    </ol>
  );
}
