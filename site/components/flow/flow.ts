import type { Mood } from "@/components/bleep/bleep";

/** The nodes of the incident graph, in the order `backend/src/bleep/graph/build.py` adds them. */
export const GRAPH_NODES = [
  "gather",
  "investigate",
  "diagnose",
  "propose",
  "approval",
  "execute",
  "verify",
  "report",
] as const;

/** The two steps before the graph: the watcher's detector, and the runner that opens and starts. */
export const OPENING_NODES = ["detect", "open"] as const;

export type GraphNode = (typeof GRAPH_NODES)[number];
export type StepId = (typeof OPENING_NODES)[number] | GraphNode;

/** Every step, top to bottom, as the page draws them. */
export const STEP_ORDER: readonly StepId[] = [...OPENING_NODES, ...GRAPH_NODES];

export interface Step {
  title: string;
  mood: Mood;
  /** One line inside the node on a wide screen. */
  short: string;
  /** The whole step, beside the diagram on a wide screen and inside the node on a phone. */
  text: string;
  /** Where it lives in the repository. */
  code: string;
}

export const STEPS: Record<StepId, Step> = {
  detect: {
    title: "Notices",
    mood: "watching",
    short: "Waits to see that a failure holds",
    text: "A container dies or keeps restarting, a URL stops answering, or a host runs low on disk or memory, or stays under heavy load. Bleep waits out a grace window first, so a container that comes back by itself opens nothing.",
    code: "backend/src/bleep/watcher/detector.py",
  },
  open: {
    title: "Opens an incident",
    mood: "watching",
    short: "Or adds the failure to one already open",
    text: "The failure opens an incident, or joins the one already open for that service. You hear about it on the dashboard, and in Telegram if you set it up. Its run starts in the background.",
    code: "backend/src/bleep/incidents/runner.py",
  },
  gather: {
    title: "Gathers",
    mood: "working",
    short: "Containers, logs, past incidents. No model yet",
    text: "The containers, the last 200 log lines of the broken ones, the host's basics, your rules, the three most similar past incidents and what Bleep knows about the service. No model call.",
    code: "backend/src/bleep/graph/nodes/gather.py",
  },
  investigate: {
    title: "Investigates",
    mood: "working",
    short: "Reads with read-only tools, 8 calls a round",
    text: "The model reads logs, events, container details and the host with read-only tools, at most 8 calls a round. Every result is capped, redacted and marked as untrusted. If the run's cost budget is spent, it stops here and reports.",
    code: "backend/src/bleep/graph/nodes/investigate.py",
  },
  diagnose: {
    title: "Diagnoses",
    mood: "working",
    short: "The cause, the evidence and how sure it is",
    text: "It names the root cause, the evidence and how sure it is. Unsure after the first round, it investigates once more, then goes on marked unsure.",
    code: "backend/src/bleep/graph/nodes/diagnose.py",
  },
  propose: {
    title: "Proposes",
    mood: "working",
    short: "One to three allowlisted steps, checked in code",
    text: "One to three steps from the allowlist, like start shop-db, or no action and exact instructions for you. Code checks the plan before anyone is asked, and a refused plan goes back to the model once.",
    code: "backend/src/bleep/graph/nodes/propose.py",
  },
  approval: {
    title: "Asks you",
    mood: "needs_you",
    short: "Waits for your answer, however long it takes",
    text: "The plan goes to the dashboard, and to Telegram if you set it up, with Yes, go ahead, No, leave it, and Tell Bleep something. The run waits, through a restart if need be. The first answer wins, a reminder goes out every hour, and nothing is ever approved by itself. A plan Bleep's own checks refused twice gets no yes button.",
    code: "backend/src/bleep/graph/nodes/approval.py",
  },
  execute: {
    title: "Runs the fix",
    mood: "working",
    short: "Only the steps you approved, in order",
    text: "It reads every target again first. If one changed since your yes, or the guard now refuses a step, nothing runs and Bleep plans again, at most three times a run. Otherwise the approved steps run in order, each logged before and after, and a failed step skips the rest.",
    code: "backend/src/bleep/graph/nodes/execute.py",
  },
  verify: {
    title: "Checks",
    mood: "working",
    short: "Healthy containers and answering URLs, or no fix",
    text: "It waits 20 seconds, then checks up to three times that every planned container runs healthy and every URL answers. A failed check investigates again, at most twice. After that the outcome is unverified, never fixed.",
    code: "backend/src/bleep/graph/nodes/verify.py",
  },
  report: {
    title: "Reports",
    mood: "all_good",
    short: "What happened, from its own record",
    text: "Code sets the outcome, and only a passed check makes it resolved. The model writes the summary, the cause and the follow-ups from Bleep's record, with anything copied from a log cut out. The report goes to every channel and into memory for the next incident.",
    code: "backend/src/bleep/graph/nodes/report.py",
  },
};

/** How an edge is drawn: the main way down, a loop back up, or an early way out to the report. */
export type EdgeKind = "next" | "back" | "end";

export interface Edge {
  from: StepId;
  to: StepId;
  kind: EdgeKind;
  /** What makes the run take this edge, in a few words. */
  when?: string;
}

/** Every edge of the incident graph (each `route` in `graph/nodes/`), and the two before it. */
export const EDGES: readonly Edge[] = [
  { from: "detect", to: "open", kind: "next" },
  { from: "open", to: "gather", kind: "next" },
  { from: "gather", to: "investigate", kind: "next" },
  { from: "investigate", to: "diagnose", kind: "next" },
  { from: "investigate", to: "report", kind: "end", when: "budget spent" },
  { from: "diagnose", to: "investigate", kind: "back", when: "unsure, once" },
  { from: "diagnose", to: "propose", kind: "next" },
  { from: "propose", to: "approval", kind: "next" },
  { from: "propose", to: "report", kind: "end", when: "nothing to run" },
  { from: "approval", to: "propose", kind: "back", when: "you tell it something" },
  { from: "approval", to: "execute", kind: "next", when: "yes" },
  { from: "approval", to: "report", kind: "end", when: "no" },
  { from: "execute", to: "propose", kind: "back", when: "target changed" },
  { from: "execute", to: "verify", kind: "next" },
  { from: "execute", to: "report", kind: "end", when: "out of re-plans" },
  { from: "verify", to: "investigate", kind: "back", when: "check failed" },
  { from: "verify", to: "report", kind: "next" },
];

/** What the watcher reads, feeding the detector. */
export const SOURCES: readonly string[] = [
  "Docker events over SSH, every 15 s",
  "Your URLs, every 30 s",
  "Disk, memory and load, every 60 s",
];

export const edgeId = (edge: Pick<Edge, "from" | "to">) => `${edge.from}-${edge.to}`;

export const edgesFrom = (step: StepId) => EDGES.filter((edge) => edge.from === step);

/** The step's ways out other than straight down, in words, for the phone and the screen reader. */
export function branches(step: StepId): string[] {
  return edgesFrom(step)
    .filter((edge) => edge.kind !== "next" || edge.when)
    .map((edge) => `${edge.when ? `${capitalise(edge.when)}: ` : ""}${STEPS[edge.to].title}`);
}

const capitalise = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

/** How long the walk stays on each step, in ms; it lingers where you come in and at the end. */
export const DWELL: Record<StepId, number> = {
  detect: 2000,
  open: 1600,
  gather: 1400,
  investigate: 1800,
  diagnose: 1600,
  propose: 1600,
  approval: 3200,
  execute: 1600,
  verify: 1800,
  report: 3200,
};
