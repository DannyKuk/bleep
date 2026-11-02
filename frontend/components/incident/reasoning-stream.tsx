import { ChevronRight, Loader2, Wrench } from "lucide-react";

import type { IncidentEvent } from "@/lib/api-types";
import { formatTime } from "@/lib/format";

interface ToolResult {
  chars: number;
  firstLine: string;
}

interface ToolStep {
  seq: number;
  at: string;
  name: string;
  args: Record<string, unknown>;
  result?: ToolResult;
}

/** Each `tool_call` with the `tool_result` that answered it: results of one tool come in call order. */
export function toolSteps(events: IncidentEvent[]): ToolStep[] {
  const steps: ToolStep[] = [];
  const waiting = new Map<string, ToolStep[]>();
  for (const { kind, payload, seq, at } of events) {
    const name = String(payload.name ?? "");
    if (kind === "tool_call") {
      const args =
        payload.args && typeof payload.args === "object"
          ? (payload.args as Record<string, unknown>)
          : {};
      const step: ToolStep = { seq, at, name, args };
      steps.push(step);
      waiting.set(name, [...(waiting.get(name) ?? []), step]);
    } else if (kind === "tool_result") {
      const step = waiting.get(name)?.shift();
      if (step) {
        step.result = {
          chars: Number(payload.chars ?? 0),
          firstLine: String(payload.summary_first_line ?? ""),
        };
      }
    }
  }
  return steps;
}

function argText(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}

function Step({ step }: { step: ToolStep }) {
  const args = Object.entries(step.args);
  return (
    <li className="flex flex-col gap-1.5 py-2.5">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm">
        <Wrench aria-hidden className="size-3.5 shrink-0 self-center text-muted-foreground" />
        <code className="font-mono font-medium">{step.name}</code>
        {args.map(([key, value]) => (
          <code key={key} className="font-mono text-xs break-all text-muted-foreground">
            {key}={argText(value)}
          </code>
        ))}
        <time dateTime={step.at} className="ml-auto text-xs text-muted-foreground">
          {formatTime(step.at)}
        </time>
      </div>
      {step.result ? (
        <details className="group ml-5.5 text-xs">
          <summary className="flex cursor-pointer list-none items-center gap-1 text-muted-foreground hover:text-foreground [&::-webkit-details-marker]:hidden">
            <ChevronRight
              aria-hidden
              className="size-3.5 transition-transform group-open:rotate-90"
            />
            Result, {step.result.chars.toLocaleString("en-US")} characters
          </summary>
          <p className="mt-1 rounded-md bg-muted px-2 py-1.5 font-mono break-all whitespace-pre-wrap">
            {step.result.firstLine || "(empty)"}
          </p>
        </details>
      ) : (
        <p className="ml-5.5 flex items-center gap-1 text-xs text-muted-foreground">
          <Loader2 aria-hidden className="size-3.5 animate-spin" />
          Running
        </p>
      )}
    </li>
  );
}

/**
 * What the model looked at while investigating: each tool call with its arguments, and the
 * result folded under it. Only the result's size and first line reach the dashboard; the full
 * output stays with the run.
 */
export function ReasoningStream({ events }: { events: IncidentEvent[] }) {
  const steps = toolSteps(events);
  if (steps.length === 0) {
    return <p className="text-sm text-muted-foreground">No tool calls yet.</p>;
  }
  return (
    <ol aria-label="Tool calls" className="divide-y divide-border">
      {steps.map((step) => (
        <Step key={step.seq} step={step} />
      ))}
    </ol>
  );
}
