import type { Mood } from "@/lib/mood";

type Point = readonly [x: number, y: number];
type Light = "ok" | "warn" | "down" | "stop";

/** One mood's pose and motion, the per-mood table of the drawing (viewBox 0 0 120 120). The
 * motion names are classes of `bleep.module.css`. */
export interface Pose {
  /** The visor's eyes as one path, and its stroke width. */
  eyes: string;
  eyeWidth: number;
  /** The visor colour of the eyes and the chest light; the visor is dark in both themes. */
  lcd: string;
  /** The status light at the end of the mic boom, as a health token. */
  light: Light;
  lightMotion?: "pulse" | "slowpulse";
  handLeft: Point;
  handRight: Point;
  hop: boolean;
  headMotion?: "tilt" | "lean";
  eyesMotion: "blink" | "wander";
  leftMotion: "float" | "flailL" | "scratch";
  rightMotion: "wave" | "float" | "float2" | "flailR" | "chin";
  question: boolean;
}

const SMILE = "M42 48q6-7 12 0 M66 48q6-7 12 0";
const ROUND = "M48 46h0 M72 46h0";

export const POSES: Record<Mood, Pose> = {
  all_good: {
    eyes: SMILE,
    eyeWidth: 4,
    lcd: "#7CF2B8",
    light: "ok",
    handLeft: [26, 92],
    handRight: [108, 72],
    hop: false,
    eyesMotion: "blink",
    leftMotion: "float",
    rightMotion: "wave",
    question: false,
  },
  working: {
    eyes: ROUND,
    eyeWidth: 9,
    lcd: "#FFD36E",
    light: "warn",
    lightMotion: "slowpulse",
    handLeft: [28, 92],
    handRight: [92, 94],
    hop: false,
    eyesMotion: "wander",
    leftMotion: "float",
    rightMotion: "float2",
    question: false,
  },
  // The "hmm": the eyes look aside at the thing that fails, the head leans towards it and one
  // hand rests on the chin. Amber like working, but the light holds steady.
  watching: {
    eyes: "M51 46h2 M75 46h2",
    eyeWidth: 8,
    lcd: "#FFD36E",
    light: "warn",
    handLeft: [28, 92],
    handRight: [68, 76],
    hop: false,
    headMotion: "lean",
    eyesMotion: "blink",
    leftMotion: "float",
    rightMotion: "chin",
    question: false,
  },
  needs_you: {
    eyes: ROUND,
    eyeWidth: 11,
    lcd: "#FF8E80",
    light: "down",
    lightMotion: "pulse",
    handLeft: [26, 62],
    handRight: [94, 62],
    hop: true,
    eyesMotion: "blink",
    leftMotion: "flailL",
    rightMotion: "flailR",
    question: false,
  },
  not_sure: {
    eyes: "M44 46h8 M72 46h0",
    eyeWidth: 7,
    lcd: "#C9C6F0",
    light: "stop",
    handLeft: [30, 54],
    handRight: [92, 96],
    hop: false,
    headMotion: "tilt",
    eyesMotion: "blink",
    leftMotion: "scratch",
    rightMotion: "float",
    question: true,
  },
  resting: {
    eyes: SMILE,
    eyeWidth: 4,
    lcd: "#7CF2B8",
    light: "ok",
    handLeft: [26, 92],
    handRight: [94, 94],
    hop: false,
    eyesMotion: "blink",
    leftMotion: "float",
    rightMotion: "float2",
    question: false,
  },
};

/** Below this size Bleep is the head only: no headset, hands or body. */
export const FULL_FIGURE_MIN_PX = 40;
