"use client";

import { useEffect, useReducer, useRef, useState } from "react";

import { Bleep } from "@/components/bleep/bleep";

import {
  dwell,
  lineOf,
  moodOf,
  PLAN_STEP,
  RUN_FACTS,
  servicesAt,
  START,
  step,
  TOOL_CALLS,
  type Health,
  type Phase,
} from "./script";

const HEALTH_WORD: Record<Health, string> = { ok: "healthy", down: "down", stopped: "stopped" };

const HEALTH_STYLE: Record<Health, string> = {
  ok: "bg-ok-bg text-ok",
  down: "bg-down-bg text-down",
  stopped: "bg-stop-bg text-stop",
};

const PHASE_TAG: Record<Phase, string> = {
  calm: "Watching",
  fails: "Something failed",
  investigating: "Investigating",
  asks: "Needs your yes",
  declined: "Left as it is",
  running: "Running the fix",
  checking: "Verifying",
  fixed: "Fixed and checked",
};

/** Starts the story once at least half of the stage is on screen, so it never plays unseen. */
function useSeen<T extends Element>() {
  const ref = useRef<T>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const node = ref.current;
    if (!node || typeof IntersectionObserver === "undefined") {
      setSeen(true);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          setSeen(true);
          observer.disconnect();
        }
      },
      { threshold: 0.5 },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return [ref, seen] as const;
}

export function Replay() {
  const [state, send] = useReducer(step, START);
  const [stage, seen] = useSeen<HTMLDivElement>();
  const { phase, shown } = state;

  useEffect(() => {
    if (!seen) return;
    const wait = dwell(state);
    if (wait === null) return;
    const timer = window.setTimeout(() => send("tick"), wait);
    return () => window.clearTimeout(timer);
  }, [seen, state]);

  const services = servicesAt(phase);
  const asking = phase === "asks";
  const done = phase === "fixed" || phase === "declined";

  return (
    <div
      ref={stage}
      data-phase={phase}
      className={`relative flex flex-col gap-5 rounded-card border-2 bg-surface p-5 shadow-[0_24px_60px_-28px_rgb(40_30_120/0.35)] transition-colors duration-500 sm:p-7 ${
        asking ? "border-accent" : "border-line"
      }`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-semibold text-ink-2">
          Host <span className="font-mono font-normal text-ink">demo</span>
        </p>
        <p
          className={`rounded-full px-3 py-1 text-xs font-bold ${
            asking
              ? "bg-accent text-accent-ink"
              : phase === "fixed"
                ? "bg-ok-bg text-ok"
                : "bg-soft text-ink-2"
          }`}
        >
          {PHASE_TAG[phase]}
        </p>
      </div>

      <ul className="grid grid-cols-2 gap-2 sm:grid-cols-4" aria-label="Containers on the host">
        {services.map((service) => (
          <li
            key={service.name}
            className={`flex flex-col gap-0.5 rounded-row px-3 py-2 transition-colors duration-500 ${HEALTH_STYLE[service.health]}`}
          >
            <span className="truncate font-mono text-[13px] text-ink">{service.name}</span>
            <span className="text-xs font-bold">{HEALTH_WORD[service.health]}</span>
          </li>
        ))}
      </ul>

      <div className="flex items-start gap-3 sm:gap-5">
        <Bleep mood={moodOf(phase)} size={96} className="mt-1 sm:hidden" />
        <Bleep mood={moodOf(phase)} size={132} className="hidden sm:block" />
        <p
          key={phase}
          aria-live="polite"
          className="bubble relative mt-3 min-h-[4.5rem] flex-1 rounded-[22px] rounded-tl-md bg-soft px-4 py-3 text-[15px] leading-snug font-semibold text-ink sm:text-base"
        >
          {lineOf(phase)}
        </p>
      </div>

      <ol className="flex min-h-[9.75rem] flex-col gap-1.5" aria-label="How Bleep got there">
        {TOOL_CALLS.slice(0, phase === "calm" || phase === "fails" ? 0 : shown).map((tool) => (
          <li
            key={tool.call}
            className="tool-line grid gap-x-3 text-[13px] sm:grid-cols-[auto_1fr]"
          >
            <span className="font-mono text-ink">{tool.call}</span>
            <span className="truncate text-ink-2">{tool.saw}</span>
          </li>
        ))}
      </ol>

      <div className="flex min-h-[3.25rem] flex-wrap items-center gap-3">
        {asking && (
          <>
            <p className="w-full text-sm font-semibold text-ink sm:w-auto sm:flex-1">
              Plan: <span className="font-mono font-normal">{PLAN_STEP}</span>
            </p>
            <button
              type="button"
              onClick={() => send("approve")}
              className="yes h-11 rounded-full bg-accent px-5 text-sm font-bold text-accent-ink"
            >
              Yes, start it
            </button>
            <button
              type="button"
              onClick={() => send("decline")}
              className="h-11 rounded-full border border-line px-5 text-sm font-bold text-ink hover:bg-soft"
            >
              Not now
            </button>
          </>
        )}
        {done && (
          <>
            {phase === "fixed" && (
              <p className="flex-1 text-sm text-ink-2">
                The real run: {RUN_FACTS.toolCalls} tool calls, {RUN_FACTS.secondsToDiagnosis} s to
                the diagnosis, {RUN_FACTS.cost}.
              </p>
            )}
            {phase === "declined" && <p className="flex-1 text-sm text-ink-2">Nothing ran.</p>}
            <button
              type="button"
              onClick={() => send("again")}
              className="h-11 rounded-full border border-line px-5 text-sm font-bold text-ink hover:bg-soft"
            >
              Play it again
            </button>
          </>
        )}
      </div>
    </div>
  );
}
