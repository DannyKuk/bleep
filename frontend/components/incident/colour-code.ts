import type { StepState } from "@/components/incident/progress";

/**
 * What a segment of the progress bar or a line of the timeline says: the bar's step states, plus
 * `warning` (went wrong without ending the run), `info` (a fact that flags nothing) and `opened`
 * (what opened the incident).
 */
export type Mark = StepState | "warning" | "info" | "opened";

export interface Look {
  /** The segment's or dot's fill. */
  fill: string;
  /** The same colour for an icon. */
  ink: string;
  /** The label's text colour, where the state gives it one. */
  tone?: string;
  /** Said to a screen reader after the label, so the colour is never the only cue. */
  word: string;
  /** Drawn with a cross: a step that went wrong, where a run stopped, a no. */
  cross: boolean;
}

/**
 * The incident page's one colour code: green done, accent happening now, red failed or stopped
 * there and nothing else, amber a warning that does not end the run, grey not reached (`line`) or
 * neutral (`stop`). What opened the incident is a hollow ring, not a state colour. Class names are
 * written out in full so Tailwind sees them.
 */
export const LOOK: Record<Mark, Look> = {
  done: { fill: "bg-ok", ink: "text-ok", word: "done", cross: false },
  current: { fill: "bg-accent", ink: "text-accent", word: "now", cross: false },
  failed: { fill: "bg-down", ink: "text-down", tone: "text-down", word: "failed", cross: true },
  stopped: {
    fill: "bg-down",
    ink: "text-down",
    tone: "text-down",
    word: "stopped here",
    cross: true,
  },
  warning: { fill: "bg-warn", ink: "text-warn", tone: "text-warn", word: "warning", cross: false },
  todo: { fill: "bg-line", ink: "text-ink-2", tone: "text-ink-2", word: "not done", cross: false },
  declined: { fill: "bg-stop", ink: "text-stop", word: "you said no", cross: true },
  info: { fill: "bg-stop", ink: "text-stop", word: "note", cross: false },
  opened: {
    fill: "border-2 border-ink-2",
    ink: "text-ink-2",
    word: "opened the incident",
    cross: false,
  },
};
