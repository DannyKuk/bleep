import attacksData from "./data/attacks.json";
import docsData from "./data/docs.json";
import faultsStrongerRuns from "./data/faults-stronger.runs.json";
import faultsStrongerData from "./data/faults-stronger.json";
import faultsData from "./data/faults.json";
import lookStrongerData from "./data/look-stronger.json";
import lookData from "./data/look.json";
import loopbackData from "./data/loopback.json";
import recoveryData from "./data/recovery.json";
import scriptedData from "./data/scripted.json";
import talkData from "./data/talk.json";
import {
  CAUSE_CHECK,
  type CaseRuns,
  type Details,
  type Outcome,
  PLAN_CHECK,
  type SuiteData,
  type SuiteKind,
} from "./extract";
import { inWords } from "./round";
import { SOURCES } from "./sources";

export type Tally = Record<Outcome, number> & { runs: number; cases: number; missed: string[] };

export function tally(cases: CaseRuns[]): Tally {
  const runs = cases.flatMap((c) => c.runs);
  const count = (outcome: Outcome) => runs.filter((run) => run === outcome).length;
  return {
    cases: cases.length,
    runs: runs.length,
    pass: count("pass"),
    fail: count("fail"),
    unmet: count("unmet"),
    missed: cases.filter((c) => c.runs.includes("fail")).map((c) => c.name),
  };
}

/** "a, b and c". */
export function listed(items: string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items.at(-1)}`;
}

const capitalised = (text: string) => text[0].toUpperCase() + text.slice(1);

/** Why a look case still misses on the stronger model, read by hand in the showcase report. */
const LOOK_SCORING: Record<string, string> = {
  "coolify-healthy-no-url":
    'both models answered rightly that nothing was wrong, and the check counted their "not a failure" as a wrong word',
};

/** The cases the model under test missed, run again on a stronger model. */
const STRONGER_DATA: Partial<Record<SuiteKind, SuiteData>> = {
  faults: faultsStrongerData as SuiteData,
  look: lookStrongerData as SuiteData,
};

/** The stronger model's runs of the cases this model missed. */
function strongerOn(kind: SuiteKind, t: Tally): CaseRuns[] {
  return STRONGER_DATA[kind]?.cases.filter((c) => t.missed.includes(c.name)) ?? [];
}

const strongerName = (kind: SuiteKind) => modelName(STRONGER_DATA[kind]?.model ?? null);

/** What the stronger model did on the look cases the model under test missed. */
function lookOnStronger(t: Tally): string {
  const tried = strongerOn("look", t);
  if (tried.length === 0) return "";
  const fixed = tried.filter((c) => c.runs.every((run) => run === "pass"));
  const still = tried.filter((c) => !fixed.includes(c));
  const runs = fixed.reduce((sum, c) => sum + c.runs.length, 0);
  const fixedLine = fixed.length
    ? ` A stronger model, ${strongerName("look")}, said it right in all ${runs} runs of ${listed(fixed.map((c) => c.name))}, where ${modelName((lookData as SuiteData).model)} found the cause but would not commit to it.`
    : "";
  const stillLine = still
    .filter((c) => LOOK_SCORING[c.name])
    .map((c) => ` On ${c.name} ${LOOK_SCORING[c.name]}.`)
    .join("");
  return fixedLine + stillLine;
}

/** How often a stronger model's fault runs passed one check, such as "Found the cause". */
function checkCount(name: string, label: string): number {
  return Object.entries(faultsStrongerRuns as Details).filter(
    ([key, run]) =>
      key.startsWith(`${name}/`) &&
      run.type === "agent" &&
      run.checks.some((check) => check.label === label && check.ok),
  ).length;
}

/** What the stronger model did on the faults the model under test missed. */
function faultsOnStronger(t: Tally): string {
  return strongerOn("faults", t)
    .map(
      (c) =>
        ` A stronger model, ${strongerName("faults")}, ran ${c.name} ${inWords(c.runs.length)} times: it proposed the expected plan in ${checkCount(c.name, PLAN_CHECK)} and found the cause in ${checkCount(c.name, CAUSE_CHECK)}.`,
    )
    .join("");
}

const missedOn = (t: Tally) =>
  t.missed.length === 0 ? "" : ` It missed on ${listed(t.missed.map((name) => `${name}`))}.`;

type SuiteText = {
  name: string;
  question: string;
  /** What the suite checks, in one line for its card. */
  checks: (cases: CaseRuns[]) => string;
  /** The card's result, such as "8 of 9 runs right". */
  result: (cases: CaseRuns[]) => string;
  /** The question's one-sentence answer. */
  answer: (cases: CaseRuns[]) => string;
  verdicts: Record<Outcome, string>;
  /** A group whose runs read differently from the rest, such as the attacks' controls. */
  groupVerdicts?: Record<string, Record<Outcome, string>>;
  groups?: Record<string, string>;
  legend: Legend | ((cases: CaseRuns[]) => Legend);
};

export type Legend = { pass: string; fail: string; unmet?: string };

const CHANNELS: Record<string, string> = {
  logs: "logs",
  docs: "docs",
  commit: "commits",
  memory: "memory",
  http_body: "error pages",
  host_basics: "host details",
  service_name: "a service's name",
  mcp: "tool replies",
};

const attacksOnly = (cases: CaseRuns[]) => cases.filter((c) => c.group !== "control");
const controls = (cases: CaseRuns[]) => cases.filter((c) => c.group === "control");

/** The fault a control runs: `control-hung-restart` runs `hung`. */
const controlFault = (name: string) => name.replace(/^control-/, "").replace(/-[a-z]+$/, "");

/** Which faults the controls missed, and whether the faults suite missed them too. */
function controlMisses(c: Tally): string {
  if (c.missed.length === 0) return "";
  const faults = [...new Set(c.missed.map(controlFault))];
  const alsoMissed = tally((faultsData as SuiteData).cases).missed;
  const same = faults.every((fault) => alsoMissed.includes(fault));
  return ` The control misses are on ${listed(faults)}${same ? `, the same miss as in Faults: a missed cause, not an attack` : ""}.`;
}

const TEXT: Record<SuiteKind, SuiteText> = {
  faults: {
    name: "Faults",
    question: "Does it find what broke?",
    checks: (cases) =>
      `${capitalised(inWords(cases.length))} incidents, each followed from the alert to the plan Bleep puts to a person.`,
    result: (cases) => {
      const t = tally(cases);
      return `${t.pass} of ${t.runs} runs found the cause and the right plan`;
    },
    answer: (cases) => {
      const t = tally(cases);
      return t.pass === t.runs
        ? `Yes. In all ${t.runs} runs Bleep named the cause and proposed the plan the case expects, which is often no step at all.`
        : `In ${t.pass} of ${t.runs} runs Bleep named the cause and proposed the plan the case expects, which is often no step at all.${missedOn(t)}${faultsOnStronger(t)}`;
    },
    verdicts: {
      pass: "found the cause, right plan",
      fail: "missed",
      unmet: "not scored",
    },
    legend: { pass: "Cause found and the right plan", fail: "Missed one of them" },
  },
  attacks: {
    name: "Attacks",
    question: "Can a log line give Bleep orders?",
    checks: (cases) => {
      const channels = [...new Set(attacksOnly(cases).map((c) => CHANNELS[c.group ?? ""]))];
      return `${capitalised(inWords(attacksOnly(cases).length))} attacks that plant orders or a false cause in ${listed(channels)}.`;
    },
    result: (cases) => {
      const t = tally(attacksOnly(cases));
      const reached = t.pass + t.fail;
      return `resisted ${t.pass} of ${reached} runs that reached the model${t.unmet ? `; ${t.unmet} never reached it` : ""}`;
    },
    answer: (cases) => {
      const t = tally(attacksOnly(cases));
      const reached = t.pass + t.fail;
      const c = tally(controls(cases));
      const head =
        t.fail === 0
          ? `No. In all ${reached} runs where the planted text reached the model, Bleep did nothing it asked.`
          : `In ${t.fail} of ${reached} runs where the planted text reached the model, Bleep did something it asked.${missedOn(t)}`;
      const unmet = t.unmet
        ? ` In ${t.unmet} more the text never reached the model, so those runs test nothing yet.`
        : "";
      const control = c.runs
        ? ` On the same faults with nothing planted it was right in ${c.pass} of ${c.runs}.${controlMisses(c)}`
        : "";
      return head + unmet + control;
    },
    verdicts: {
      pass: "resisted",
      fail: "did what the planted text asked",
      unmet: "the planted text never reached the model",
    },
    groupVerdicts: {
      control: {
        pass: "nothing planted, found the cause",
        fail: "nothing planted, missed the cause",
        unmet: "not scored",
      },
    },
    groups: {
      ...Object.fromEntries(
        Object.entries(CHANNELS).map(([key, words]) => [key, `Through ${words}`]),
      ),
      control: "Controls: the same faults with nothing planted, so a red dot is a missed cause",
    },
    legend: (cases) => {
      const followed = tally(attacksOnly(cases)).fail > 0;
      const controlMissed = tally(controls(cases)).fail > 0;
      return {
        pass: "Resisted, or a control right",
        fail:
          followed && controlMissed
            ? "Did what it asked, or a control's cause missed"
            : controlMissed
              ? "A control's cause missed; no attack was followed"
              : "Did what it asked",
        unmet: "Never reached the model",
      };
    },
  },
  talk: {
    name: "Talk",
    question: "Can you just ask it?",
    checks: (cases) =>
      `${capitalised(inWords(cases.length))} messages a person might write to Bleep in its chat, in English and German.`,
    result: (cases) => {
      const t = tally(cases);
      return `${t.pass} of ${t.runs} read right`;
    },
    answer: (cases) => {
      const t = tally(cases);
      return t.pass === t.runs
        ? `Yes. Bleep read all ${t.runs} messages right: what was asked, what to do, and in which language to answer.`
        : `Mostly. Bleep read ${t.pass} of ${t.runs} messages right: what was asked, what to do, and in which language to answer.${missedOn(t)}`;
    },
    verdicts: { pass: "read right", fail: "missed", unmet: "not scored" },
    groups: { en: "In English", de: "In German" },
    legend: { pass: "Read right", fail: "Missed" },
  },
  look: {
    name: "Look",
    question: "Does it look before it says healthy?",
    checks: (cases) =>
      `${capitalised(inWords(cases.length))} services to look at on request, some healthy and some quietly broken.`,
    result: (cases) => {
      const t = tally(cases);
      return `${t.pass} of ${t.runs} said it right`;
    },
    answer: (cases) => {
      const t = tally(cases);
      return `Bleep reads every container's logs and checks every URL before it answers, and said rightly whether the service was healthy in ${t.pass} of ${t.runs} runs.${missedOn(t)}${lookOnStronger(t)}`;
    },
    verdicts: { pass: "said it right", fail: "missed", unmet: "not scored" },
    legend: { pass: "Said it right", fail: "Missed" },
  },
  loopback: {
    name: "Loop back",
    question: "What does it do when its fix didn't work?",
    checks: (cases) =>
      `${capitalised(inWords(cases.length))} approved fixes that failed or proved nothing, sent back to Bleep to think again.`,
    result: (cases) => {
      const t = tally(cases);
      return `${t.pass} of ${t.runs} looked again instead of repeating the fix`;
    },
    answer: (cases) => {
      const t = tally(cases);
      return t.pass === t.runs
        ? `It looks again. In all ${t.runs} runs Bleep did not repeat what failed, and either named the real cause or read what could show it.`
        : `In ${t.pass} of ${t.runs} runs Bleep did not repeat what failed, and either named the real cause or read what could show it.${missedOn(t)}`;
    },
    verdicts: { pass: "looked again", fail: "missed", unmet: "not scored" },
    legend: { pass: "Looked again", fail: "Missed" },
  },
  recovery: {
    name: "Recovery",
    question: "Can it say why a container came back on its own?",
    checks: (cases) =>
      `${capitalised(inWords(cases.length))} containers that died and restarted by themselves; Bleep writes one line on why.`,
    result: (cases) => {
      const t = tally(cases);
      return `${t.pass} of ${t.runs} lines named the cause`;
    },
    answer: (cases) => {
      const t = tally(cases);
      return t.pass === t.runs
        ? `Yes. All ${t.runs} lines named the cause the logs show, and none took a cause planted in them.`
        : `In ${t.pass} of ${t.runs} runs the line named the cause the logs show.${missedOn(t)}`;
    },
    verdicts: { pass: "named the cause", fail: "missed", unmet: "not scored" },
    legend: { pass: "Named the cause", fail: "Missed" },
  },
  scripted: {
    name: "Look-alike names",
    question: "Can a log line point it at the wrong container?",
    checks: (cases) =>
      `${capitalised(inWords(cases.length))} logs that push a step on a protected container or one named by a look-alike id.`,
    result: (cases) => {
      const t = tally(cases);
      return t.unmet === t.runs
        ? `never wrote the pushed step in ${t.runs} runs`
        : `the guard held in ${t.pass} of ${t.pass + t.fail} runs that wrote it`;
    },
    answer: (cases) => {
      const t = tally(cases);
      const wrote = t.pass + t.fail;
      return t.unmet === t.runs
        ? `In all ${t.runs} runs Bleep never wrote the step the log pushed, so the guard behind it had nothing to stop.`
        : `In ${t.unmet} of ${t.runs} runs Bleep never wrote the step the log pushed; where it did, the guard stopped it in ${t.pass} of ${wrote}.`;
    },
    verdicts: {
      pass: "wrote the step, the guard stopped it",
      fail: "the step reached the approval",
      unmet: "never wrote the pushed step",
    },
    legend: {
      pass: "Wrote it, the guard stopped it",
      fail: "Reached the approval",
      unmet: "Never wrote the pushed step",
    },
  },
  docs: {
    name: "Docs search",
    question: "Does it find the right page in the docs?",
    checks: (cases) =>
      `${capitalised(inWords(cases.length))} questions about Docker in English and German, each with the doc page that answers it.`,
    result: (cases) => {
      const t = tally(cases);
      return `right page in the top five for ${t.pass} of ${t.runs}`;
    },
    answer: (cases) => {
      const t = tally(cases);
      return `For ${t.pass} of ${t.runs} questions the page that answers it came back among the five pages Bleep reads.${missedOn(t)}`;
    },
    verdicts: { pass: "found the page", fail: "missed the page", unmet: "not scored" },
    legend: { pass: "Found the page", fail: "Missed it" },
  },
};

const DATA: Record<SuiteKind, SuiteData> = {
  faults: faultsData,
  attacks: attacksData,
  talk: talkData,
  look: lookData,
  loopback: loopbackData,
  recovery: recoveryData,
  scripted: scriptedData,
  docs: docsData,
} as Record<SuiteKind, SuiteData>;

/** The order the page shows them in. */
const ORDER: SuiteKind[] = [
  "faults",
  "attacks",
  "talk",
  "loopback",
  "look",
  "recovery",
  "scripted",
  "docs",
];

export type Suite = SuiteText & {
  kind: SuiteKind;
  data: SuiteData;
  /** The cases this model missed, run again on a stronger model, when there was such a run. */
  stronger: SuiteData | null;
  report: string;
};

export const SUITES: Suite[] = ORDER.map((kind) => ({
  kind,
  ...TEXT[kind],
  data: DATA[kind],
  stronger: STRONGER_DATA[kind] ?? null,
  report: SOURCES[kind].report,
}));

/** A suite's legend words for its cases. */
export const legendOf = (suite: Suite): Legend =>
  typeof suite.legend === "function" ? suite.legend(suite.data.cases) : suite.legend;

/** How many runs each case had: "one run per case", "three runs per case", "one to three runs". */
export function runsPerCase(cases: CaseRuns[]): string {
  const counts = [...new Set(cases.map((c) => c.runs.length))].sort((a, b) => a - b);
  if (counts.length === 1) {
    return `${inWords(counts[0])} run${counts[0] === 1 ? "" : "s"} per case`;
  }
  return `${inWords(counts[0])} to ${inWords(counts.at(-1) ?? 0)} runs per case`;
}

/** Cases whose runs did not all end the same way: the spread a single run hides. */
export const mixedCases = (cases: CaseRuns[]) =>
  cases.filter((c) => new Set(c.runs).size > 1).map((c) => c.name);

/** A model id as the page names it: `openai/gpt-6-luna` is gpt-6-luna. */
export const modelName = (model: string | null) => (model ? model.split("/").at(-1) : null);
