import { ChevronRight, Loader2 } from "lucide-react";

import { toolSentence } from "@/components/incident/tool-sentences";
import { useTimeZone } from "@/components/preferences";
import type { IncidentEvent } from "@/lib/api-types";
import { formatCount, formatTime } from "@/lib/format";
import { useLanguage, useT } from "@/lib/i18n";

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
  const zone = useTimeZone();
  const language = useLanguage();
  const t = useT();
  const args = Object.entries(step.args);
  const sentence = toolSentence(step.name, t);
  const time = (
    <time dateTime={step.at} className="font-mono text-xs text-ink-2">
      {formatTime(step.at, zone, language)}
    </time>
  );
  return (
    <li className="flex flex-col gap-1.5 rounded-row bg-soft px-4 py-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        {sentence ? (
          <>
            <span className="text-[15px] font-bold">{sentence}</span>
            <span className="ml-auto text-xs text-ink-2">
              <code className="font-mono">{step.name}</code> · {time}
            </span>
          </>
        ) : (
          <>
            <code className="font-mono text-sm">{step.name}</code>
            <span className="ml-auto">{time}</span>
          </>
        )}
      </div>
      {args.length > 0 && (
        <div className="flex flex-wrap gap-x-3 gap-y-0.5">
          {args.map(([key, value]) => (
            <code key={key} className="font-mono text-xs break-all text-ink-2">
              {key}={argText(value)}
            </code>
          ))}
        </div>
      )}
      {step.result ? (
        <details className="group text-xs">
          <summary className="-mx-1 flex w-fit cursor-pointer list-none items-center gap-1 rounded-full px-1 py-1 text-ink-2 hover:text-ink focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none pointer-coarse:min-h-11 [&::-webkit-details-marker]:hidden">
            <ChevronRight
              aria-hidden
              className="size-3.5 transition-transform group-open:rotate-90 motion-reduce:transition-none"
            />
            {t.reasoning.result(formatCount(step.result.chars, language))}
          </summary>
          <p className="mt-1 rounded-field bg-surface px-3 py-2 font-mono break-all whitespace-pre-wrap">
            {step.result.firstLine || t.reasoning.emptyResult}
          </p>
        </details>
      ) : (
        <p className="flex items-center gap-1 text-xs text-ink-2">
          <Loader2 aria-hidden className="size-3.5 motion-safe:animate-spin" />
          {t.reasoning.running}
        </p>
      )}
    </li>
  );
}

/**
 * How Bleep got to its diagnosis: each tool call as a plain sentence, with the tool's name, its
 * arguments and the time in mono, and the result folded under it. Only the result's size and
 * first line reach the dashboard; the full output stays with the run.
 */
export function ReasoningStream({ events }: { events: IncidentEvent[] }) {
  const t = useT();
  const steps = toolSteps(events);
  if (steps.length === 0) {
    return <p className="text-sm text-ink-2">{t.reasoning.empty}</p>;
  }
  return (
    <ol aria-label={t.reasoning.label} className="flex flex-col gap-2">
      {steps.map((step) => (
        <Step key={step.seq} step={step} />
      ))}
    </ol>
  );
}
