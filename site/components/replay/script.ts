import type { Mood } from "@/lib/mood";

/**
 * The hero's replay of one incident, taken from a real run: run 1 of the db-down scenario in
 * docs/evals/scenarios-2026-11-05-3.md (the same five tool calls in the same order, the same
 * diagnosis and plan). Time is shortened; the visitor plays the human who says yes or no.
 */

export type Health = "ok" | "down" | "stopped";

export type Phase =
  "calm" | "fails" | "investigating" | "asks" | "declined" | "running" | "checking" | "fixed";

export interface ToolCall {
  call: string;
  saw: string;
}

export const TOOL_CALLS: readonly ToolCall[] = [
  { call: "list_containers()", saw: "shop-db exited, shop-web unhealthy" },
  { call: 'inspect_container("shop-db")', saw: "exited (0): it was stopped, it did not crash" },
  { call: 'container_logs("shop-db", tail=100)', saw: "database system is shut down" },
  { call: "recent_events(minutes=60)", saw: "shop-db: kill, stop, die" },
  { call: 'inspect_container("shop-web")', saw: "health check failing since shop-db stopped" },
];

export const PLAN_STEP = "docker.start shop-db";

export const RUN_FACTS = {
  toolCalls: TOOL_CALLS.length,
  secondsToDiagnosis: 55,
  cost: "$0.0028",
  report: "docs/evals/scenarios-2026-11-05-3.md",
} as const;

export interface ReplayState {
  phase: Phase;
  /** How many of TOOL_CALLS are on screen. */
  shown: number;
}

export type ReplayEvent = "tick" | "approve" | "decline" | "again";

export const START: ReplayState = { phase: "calm", shown: 0 };

/**
 * The next state. A `tick` moves the story on by itself, but never past `asks`: only `approve`
 * does, which is the whole point of Bleep.
 */
export function step(state: ReplayState, event: ReplayEvent): ReplayState {
  if (event === "again") return START;
  if (event === "approve") return state.phase === "asks" ? { ...state, phase: "running" } : state;
  if (event === "decline") return state.phase === "asks" ? { ...state, phase: "declined" } : state;
  switch (state.phase) {
    case "calm":
      return { ...state, phase: "fails" };
    case "fails":
      return { ...state, phase: "investigating" };
    case "investigating":
      return state.shown < TOOL_CALLS.length
        ? { ...state, shown: state.shown + 1 }
        : { ...state, phase: "asks" };
    case "running":
      return { ...state, phase: "checking" };
    case "checking":
      return { ...state, phase: "fixed" };
    default:
      return state;
  }
}

/** How long a state stays on screen before the next tick, in ms; null waits for the visitor. */
export function dwell(state: ReplayState): number | null {
  switch (state.phase) {
    case "calm":
      return 1600;
    case "fails":
      return 1700;
    case "investigating":
      return state.shown === 0 ? 700 : 950;
    case "running":
      return 1500;
    case "checking":
      return 1700;
    default:
      return null;
  }
}

export function moodOf(phase: Phase): Mood {
  switch (phase) {
    case "calm":
      return "resting";
    case "fails":
      return "watching";
    case "investigating":
    case "running":
    case "checking":
      return "working";
    case "asks":
      return "needs_you";
    case "declined":
      return "not_sure";
    case "fixed":
      return "all_good";
  }
}

export interface Service {
  name: string;
  health: Health;
}

export function servicesAt(phase: Phase): Service[] {
  const dbBack = phase === "checking" || phase === "fixed";
  const webBack = phase === "fixed";
  const broken = phase !== "calm";
  return [
    { name: "shop-proxy", health: "ok" },
    { name: "shop-web", health: broken && !webBack ? "down" : "ok" },
    { name: "shop-db", health: broken && !dbBack ? "stopped" : "ok" },
    { name: "shop-worker", health: "ok" },
  ];
}

/** What Bleep says in its bubble. */
export function lineOf(phase: Phase): string {
  switch (phase) {
    case "calm":
      return "All four containers are fine. I check again every 15 seconds.";
    case "fails":
      return "shop-web is failing its health check. Is it holding? It is. I open an incident.";
    case "investigating":
      return "Let me look, the way you would.";
    case "asks":
      return "shop-db was stopped, so shop-web cannot reach its database. I want to start shop-db. Nothing runs until you say yes.";
    case "declined":
      return "Okay. I changed nothing. The incident stays open for when you want it.";
    case "running":
      return "Starting shop-db, the one step you approved.";
    case "checking":
      return "Checking that shop-web is healthy and the shop answers again.";
    case "fixed":
      return "Fixed and checked: the containers are healthy and the shop answers 200.";
  }
}
