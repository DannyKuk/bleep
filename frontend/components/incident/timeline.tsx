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

/** The dot's colour: red needs the human or went wrong, amber is under way, accent is the
 * human's answer, green went well, line is a step done. */
export type Dot = "down" | "warn" | "accent" | "ok" | "line";

// Written out in full so Tailwind sees the class names.
const DOT: Record<Dot, string> = {
  down: "bg-down",
  warn: "bg-warn",
  accent: "bg-accent",
  ok: "bg-ok",
  line: "bg-line",
};

interface Entry {
  seq: number;
  at: string;
  dot: Dot;
  text: string;
  running?: boolean;
  tone?: string;
}

const ACTION_DOT: Record<string, Dot> = { started: "warn", done: "ok", failed: "down" };

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

/** The incident's steps, one line each; a node's start and finish become one line. */
export function timelineEntries(events: IncidentEvent[]): Entry[] {
  const entries: Entry[] = [];
  const open = new Map<string, Entry>();
  for (const { kind, payload, seq, at } of events) {
    const node = String(payload.node ?? "");
    const [running, done] = NODES[node] ?? [`Running ${node}`, `Finished ${node}`];
    switch (kind) {
      case "node_started": {
        // The approval node runs for as long as the plan waits for the human.
        const dot = node === "approval" ? "down" : "warn";
        const entry: Entry = { seq, at, dot, text: running, running: true };
        // A node that stopped at an interrupt (Approval waiting for the human) starts again on
        // resume without having finished, so its earlier line is no longer running.
        const interrupted = open.get(node);
        if (interrupted) Object.assign(interrupted, { dot: "line", running: false });
        open.set(node, entry);
        entries.push(entry);
        break;
      }
      case "node_finished": {
        const entry = open.get(node);
        if (entry) {
          Object.assign(entry, { dot: "line", text: done, running: false });
          open.delete(node);
        } else {
          entries.push({ seq, at, dot: "line", text: done });
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
          dot: "line",
          text: payload.kind === "no_action" ? "Proposed no action" : "Proposed a plan",
        });
        break;
      case "approval_requested":
        entries.push({ seq, at, dot: "line", text: "Asked for approval" });
        break;
      case "decision": {
        // The wait ends with the answer, before Approval resumes and finishes.
        const waiting = open.get("approval");
        if (waiting) Object.assign(waiting, { dot: "line", running: false });
        open.delete("approval");
        entries.push({
          seq,
          at,
          dot: "accent",
          text: decisionText(payload),
        });
        break;
      }
      case "action": // Execute, one per step status change
        entries.push({
          seq,
          at,
          dot: ACTION_DOT[String(payload.status)] ?? "line",
          text: `${String(payload.action ?? "")} ${String(payload.target ?? "")}: ${String(payload.status ?? "")}`,
          tone: payload.status === "failed" ? "text-down" : undefined,
        });
        break;
      case "verification":
        entries.push({
          seq,
          at,
          dot: payload.ok ? "ok" : "warn",
          text: `Check ${String(payload.attempt ?? 1)} ${payload.ok ? "passed" : "failed"}`,
          tone: payload.ok ? undefined : "text-warn",
        });
        break;
      case "notice":
        entries.push({
          seq,
          at,
          dot: "warn",
          text: String(payload.text ?? ""),
          tone: "text-warn",
        });
        break;
      default:
        entries.push({ seq, at, dot: "line", text: kind.replaceAll("_", " ") });
    }
  }
  return entries;
}

const END: Partial<Record<Incident["status"], { text: string; dot: Dot }>> = {
  resolved: { text: "Resolved", dot: "ok" },
  closed: { text: "Closed", dot: "line" },
  failed: { text: "Failed", dot: "down" },
};

/** The incident's own start and end around its events: what opened it, and how it ended. */
function withEnds(entries: Entry[], incident: Incident): Entry[] {
  const opened: Entry = { seq: -1, at: incident.opened_at, dot: "down", text: incident.title };
  const end = END[incident.status];
  if (end) {
    // An ended incident has nothing running; a node that never finished is where it stopped.
    for (const entry of entries.filter(({ running }) => running)) {
      Object.assign(entry, { running: false, dot: incident.status === "failed" ? "down" : "line" });
    }
  }
  const closed: Entry[] =
    end && incident.closed_at
      ? [{ seq: Number.MAX_SAFE_INTEGER, at: incident.closed_at, ...end }]
      : [];
  return [opened, ...entries, ...closed];
}

/**
 * What happened to the incident, in order: steps, the question, the answer, notices. Each line
 * has a coloured dot; the words say the same, so the colour is never the only cue. With the
 * incident, the first line is what opened it and the last how it ended.
 */
export function Timeline({ events, incident }: { events: IncidentEvent[]; incident?: Incident }) {
  const steps = timelineEntries(events);
  if (steps.length === 0 && !incident) {
    return <p className="text-sm text-ink-2">Nothing has happened yet.</p>;
  }
  const entries = incident ? withEnds(steps, incident) : steps;
  return (
    <ol aria-label="Timeline" className="flex flex-col gap-3">
      {entries.map(({ seq, at, dot, text, running, tone }) => (
        <li key={seq} className="flex items-start gap-2.5 text-sm">
          <span
            aria-hidden
            className={cn(
              "mt-[5px] size-2.5 shrink-0 rounded-full",
              DOT[dot],
              running && "motion-safe:animate-pulse",
            )}
          />
          <span className={cn("min-w-0 flex-1 break-words", running && "font-extrabold", tone)}>
            {text}
          </span>
          <time dateTime={at} className="shrink-0 font-mono text-xs text-ink-2">
            {formatTime(at)}
          </time>
        </li>
      ))}
    </ol>
  );
}
