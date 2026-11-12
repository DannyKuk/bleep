import type { Mood } from "@/lib/mood";
import { cn } from "@/lib/utils";

import styles from "./bleep.module.css";
import { FULL_FIGURE_MIN_PX, POSES, type Pose } from "./poses";

export type { Mood } from "@/lib/mood";

const LIGHT_FILL: Record<Pose["light"], string> = {
  ok: "fill-ok",
  warn: "fill-warn",
  down: "fill-down",
  stop: "fill-stop",
};

export interface BleepProps {
  mood: Mood;
  /** Width and height in px. Below 40 px Bleep is the head only. */
  size: number;
  className?: string;
  /** What Bleep's picture says to a screen reader; without it the picture is decorative, since
   * the state it shows is always written next to it as well. */
  title?: string;
}

/**
 * Bleep, the on-call bot, in a mood. It always breathes and blinks, and each mood adds its own
 * motion; a new mood moves the same figure into its new pose, so the drawing never swaps. With
 * `prefers-reduced-motion: reduce` it stands still in the pose.
 */
export function Bleep({ mood, size, className, title }: BleepProps) {
  const full = size >= FULL_FIGURE_MIN_PX;
  const label = title ? { role: "img", "aria-label": title } : { "aria-hidden": true };
  return (
    <svg
      viewBox="0 0 120 120"
      width={size}
      height={size}
      data-mood={mood}
      data-figure={full ? "full" : "head"}
      className={cn(styles.bleep, "shrink-0", className)}
      {...label}
    >
      {title && <title>{title}</title>}
      {full ? <Figure pose={POSES[mood]} mood={mood} /> : <HeadOnly pose={POSES[mood]} />}
    </svg>
  );
}

function Figure({ pose, mood }: { pose: Pose; mood: Mood }) {
  return (
    <g className={styles.breathe}>
      <g className={cn(pose.hop && styles.hop)} data-part="figure">
        <rect data-part="body" x="40" y="76" width="40" height="28" rx="12" className="fill-blob" />
        <circle
          data-part="chest-light"
          cx="60"
          cy="90"
          r="4"
          className={styles.light}
          style={{ fill: pose.lcd }}
        />
        <g data-part="head" className={cn(styles.head, pose.headMotion && styles[pose.headMotion])}>
          <path
            data-part="headset"
            d="M24 44Q24 8 60 8T96 44"
            fill="none"
            strokeWidth="5"
            strokeLinecap="round"
            className="stroke-cup"
          />
          <rect x="26" y="22" width="68" height="50" rx="24" className="fill-blob" />
          <rect x="34" y="33" width="52" height="26" rx="13" className="fill-screen" />
          <g data-part="eyes" className={cn(styles.eyes, styles[pose.eyesMotion])}>
            {/* Keyed by mood: a new mood remounts the eyes, which open on the new expression. */}
            <g key={mood} className={styles.react}>
              <path
                d={pose.eyes}
                fill="none"
                strokeWidth={pose.eyeWidth}
                strokeLinecap="round"
                style={{ stroke: pose.lcd }}
              />
            </g>
          </g>
          <rect x="15" y="34" width="14" height="22" rx="7" className="fill-cup" />
          <rect x="91" y="34" width="14" height="22" rx="7" className="fill-cup" />
          <path
            d="M99 55q1 13-18 14"
            fill="none"
            strokeWidth="3.5"
            strokeLinecap="round"
            className="stroke-cup"
          />
          <circle
            data-part="light"
            data-light={pose.light}
            cx="80"
            cy="69"
            r="4.5"
            className={cn(
              styles.light,
              LIGHT_FILL[pose.light],
              pose.lightMotion && styles[pose.lightMotion],
            )}
          />
        </g>
        <Hand part="hand-left" side={styles.handLeft} motion={pose.leftMotion} at={pose.handLeft} />
        <Hand
          part="hand-right"
          side={styles.handRight}
          motion={pose.rightMotion}
          at={pose.handRight}
        />
        {pose.ticks && (
          <path
            data-part="ticks"
            d="M118 40l4-6 M122 50h7 M118 60l4 6"
            fill="none"
            strokeWidth="2.5"
            strokeLinecap="round"
            className={cn(styles.ticks, "stroke-warn")}
          />
        )}
        {pose.question && (
          <path
            data-part="question"
            d="M100 12a5 5 0 1 1 6 5v4 M106 27h0"
            fill="none"
            strokeWidth="4"
            strokeLinecap="round"
            className={cn(styles.question, "stroke-ink-2")}
          />
        )}
      </g>
    </g>
  );
}

/** A floating hand: a dot (a zero-length path with a round cap) drawn in front of the head. The
 * outer group plays the mood's motion around a fixed point; the inner one moves to the pose's
 * place with a transition. */
function Hand({
  part,
  side,
  motion,
  at,
}: {
  part: string;
  side: string | undefined;
  motion: Pose["leftMotion"] | Pose["rightMotion"];
  at: Pose["handLeft"];
}) {
  return (
    <g data-part={part} data-motion={motion} className={cn(side, styles[motion])}>
      <g className={styles.place} style={{ transform: `translate(${at[0]}px, ${at[1]}px)` }}>
        <path d="M0 0h0" strokeWidth="13" strokeLinecap="round" className="stroke-hand" />
      </g>
    </g>
  );
}

/** The small Bleep (header logo, favicon): head, visor and two round eyes in the mood's colour,
 * still blinking. */
function HeadOnly({ pose }: { pose: Pose }) {
  return (
    <g data-part="head">
      <rect x="10" y="18" width="100" height="84" rx="38" className="fill-blob" />
      <rect x="22" y="36" width="76" height="44" rx="22" className="fill-screen" />
      <g data-part="eyes" className={cn(styles.headEyes, styles.blink)}>
        <path
          d="M45 58h0 M75 58h0"
          fill="none"
          strokeWidth="20"
          strokeLinecap="round"
          className={styles.eyeColour}
          style={{ stroke: pose.lcd }}
        />
      </g>
    </g>
  );
}
