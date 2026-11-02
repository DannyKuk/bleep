import {
  CheckCircle2,
  Circle,
  Gavel,
  Info,
  Loader2,
  type LucideIcon,
  MessageSquare,
  Send,
} from "lucide-react";

import type { IncidentEvent } from "@/lib/api-types";
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

interface Entry {
  seq: number;
  at: string;
  icon: LucideIcon;
  text: string;
  running?: boolean;
  tone?: string;
}

function channel(payload: Record<string, unknown>): string {
  const via = payload.via;
  return via === "telegram" || via === "dashboard" ? CHANNEL[via] : "a channel";
}

function decisionText(payload: Record<string, unknown>): string {
  const where = channel(payload);
  if (payload.kind === "approve") return `Approved on ${where}`;
  if (payload.kind === "reject") return `Rejected on ${where}`;
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
        const entry: Entry = { seq, at, icon: Loader2, text: running, running: true };
        open.set(node, entry);
        entries.push(entry);
        break;
      }
      case "node_finished": {
        const entry = open.get(node);
        if (entry) {
          Object.assign(entry, { icon: CheckCircle2, text: done, running: false });
          open.delete(node);
        } else {
          entries.push({ seq, at, icon: CheckCircle2, text: done });
        }
        break;
      }
      case "tool_call":
      case "tool_result":
        break; // the reasoning stream shows these
      case "proposal":
        entries.push({
          seq,
          at,
          icon: Circle,
          text: payload.kind === "no_action" ? "Proposed no action" : "Proposed a plan",
        });
        break;
      case "approval_requested":
        entries.push({ seq, at, icon: Send, text: "Asked for approval" });
        break;
      case "decision":
        entries.push({
          seq,
          at,
          icon: payload.kind === "instruct" ? MessageSquare : Gavel,
          text: decisionText(payload),
        });
        break;
      case "notice":
        entries.push({
          seq,
          at,
          icon: Info,
          text: String(payload.text ?? ""),
          tone: "text-health-degraded",
        });
        break;
      default:
        entries.push({ seq, at, icon: Circle, text: kind.replaceAll("_", " ") });
    }
  }
  return entries;
}

/** What happened to the incident, in order: steps, the question, the answer, notices. */
export function Timeline({ events }: { events: IncidentEvent[] }) {
  const entries = timelineEntries(events);
  if (entries.length === 0) {
    return <p className="text-sm text-muted-foreground">Nothing has happened yet.</p>;
  }
  return (
    <ol aria-label="Timeline" className="flex flex-col gap-2">
      {entries.map(({ seq, at, icon: Icon, text, running, tone }) => (
        <li key={seq} className="flex items-start gap-2 text-sm">
          <Icon
            aria-hidden
            className={cn(
              "mt-0.5 size-4 shrink-0 text-muted-foreground",
              running && "animate-spin",
              tone,
            )}
          />
          <span className={cn("min-w-0 flex-1", tone)}>{text}</span>
          <time dateTime={at} className="shrink-0 text-xs text-muted-foreground">
            {formatTime(at)}
          </time>
        </li>
      ))}
    </ol>
  );
}
