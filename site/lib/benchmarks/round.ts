import data from "./round.json";

export type Fault = { rootCause: boolean; exactPlan: boolean; unnecessaryAction: boolean };
export type Attack = { resisted: boolean; reached: boolean };

/** One model's runs in the round: every fault and attack case once, and the control. */
export type ModelResult = {
  name: string;
  model: string;
  route: "OpenRouter" | "Anthropic" | "local";
  commit: string;
  /** The raw batch files in docs/evals/data/, without their extension. */
  scenarios: string;
  injection: string;
  faults: Record<string, Fault>;
  attacks: Record<string, Attack>;
  controlRight: boolean;
  unsure: number;
  runs: number;
  /** Mean seconds from the incident opening to the diagnosis: the model's part, without the
   * 30 to 60 s the watcher takes to open the incident. */
  secondsToDiagnosis: number;
  costPerRun: number;
  /** Anything that makes a number not comparable, such as a local model spilling into RAM. */
  note: string;
};

export type Round = {
  date: string;
  judge: string;
  report: string;
  decision: string;
  findings: string[];
  models: ModelResult[];
};

export const round = data as Round;

export const count = <T>(cases: Record<string, T>, right: (value: T) => boolean) =>
  Object.values(cases).filter(right).length;

export function formatCost(usd: number): string {
  return usd === 0 ? "$0" : `$${usd.toFixed(4)}`;
}

export const longDate = (iso: string) =>
  new Date(`${iso}T12:00:00Z`).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });

const WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"];

/** A small count in words, as the site's prose writes it: "six models", "ten cases". */
export const inWords = (n: number) => WORDS[n] ?? String(n);
