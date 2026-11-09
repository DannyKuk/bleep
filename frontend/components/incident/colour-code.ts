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
  /** The marker: a cross for a step that went wrong, where a run stopped or a no; an alert for
   * what broke and opened the incident; a plain dot otherwise. */
  icon: "dot" | "cross" | "alert";
}

/**
 * The incident page's one colour code: green done, accent happening now, red failed or stopped
 * there and nothing else, amber a warning that does not end the run, grey not reached (`line`) or
 * neutral (`stop`). What opened the incident is red too, since something broke, but with an alert
 * rather than a cross, which is kept for Bleep's own steps. Class names are written out in full so
 * Tailwind sees them.
 */
export const LOOK: Record<Mark, Look> = {
  done: { fill: "bg-ok", ink: "text-ok", word: "done", icon: "dot" },
  current: { fill: "bg-accent", ink: "text-accent", word: "now", icon: "dot" },
  failed: { fill: "bg-down", ink: "text-down", tone: "text-down", word: "failed", icon: "cross" },
  stopped: {
    fill: "bg-down",
    ink: "text-down",
    tone: "text-down",
    word: "stopped here",
    icon: "cross",
  },
  warning: { fill: "bg-warn", ink: "text-warn", tone: "text-warn", word: "warning", icon: "dot" },
  todo: { fill: "bg-line", ink: "text-ink-2", tone: "text-ink-2", word: "not done", icon: "dot" },
  declined: { fill: "bg-stop", ink: "text-stop", word: "you said no", icon: "cross" },
  info: { fill: "bg-stop", ink: "text-stop", word: "note", icon: "dot" },
  opened: { fill: "bg-down", ink: "text-down", word: "what broke", icon: "alert" },
};
