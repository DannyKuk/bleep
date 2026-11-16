import type { Metadata } from "next";

import { DotLegend } from "~/components/benchmarks/dot";
import { SuiteGrid } from "~/components/benchmarks/dot-grid";
import { Legend, Results } from "~/components/benchmarks/results";
import { RunDialogProvider } from "~/components/benchmarks/run-dialog";
import { REPO, SiteFooter, SiteHeader, blob } from "~/components/chrome";
import { type Round, inWords, longDate, round } from "~/lib/benchmarks/round";
import {
  SUITES,
  type Suite,
  mixedCases,
  modelName,
  runsPerCase,
  tally,
} from "~/lib/benchmarks/suites";

const totalRuns = SUITES.reduce((sum, suite) => sum + tally(suite.data.cases).runs, 0);

export const metadata: Metadata = {
  title: "What Bleep is tested on, Bleep",
  description: `${totalRuns} runs over ${inWords(SUITES.length)} suites: faults found, attacks resisted, messages read, and ${inWords(round.models.length)} models compared. Every run opens to what Bleep found and did.`,
};

/** The commit most models ran on, and the models that ran on another one. */
function commitText({ models }: Round) {
  const byCommit = Map.groupBy(models, (model) => model.commit);
  const [main] = [...byCommit.entries()].sort((a, b) => b[1].length - a[1].length)[0];
  const others = models
    .filter((model) => model.commit !== main)
    .map((model) => `${model.name} on ${model.commit}`);
  return others.length === 0 ? main : `${main} (${others.join(", ")})`;
}

const capitalised = (text: string) => text[0].toUpperCase() + text.slice(1);

const LINK = "font-bold text-accent underline-offset-4 hover:underline";

const dateText = (dates: string[]) =>
  dates.length === 1
    ? longDate(dates[0])
    : `${longDate(dates[0])} to ${longDate(dates.at(-1) ?? dates[0])}`;

function judgeText(suite: Suite) {
  if (suite.kind === "docs") return "By code; no chat model involved";
  const judged = suite.kind === "attacks" ? "the controls' root causes" : "root causes";
  if (suite.data.judge === "manual") return `By code; ${judged} graded by hand`;
  if (suite.data.judge) return `By code; ${judged} judged by ${suite.data.judge}`;
  return "By code";
}

function SuiteCard({ suite }: { suite: Suite }) {
  return (
    <li>
      <a
        href={`#${suite.kind}`}
        className="flex h-full flex-col gap-2 rounded-card border border-line bg-surface p-5 transition-colors hover:border-accent"
      >
        <span className="text-sm font-extrabold tracking-wide text-ink-2 uppercase">
          {suite.name}
        </span>
        <span className="text-lg leading-snug font-extrabold">
          {capitalised(suite.result(suite.data.cases))}
        </span>
        <span className="text-sm leading-relaxed text-ink-2">{suite.checks(suite.data.cases)}</span>
      </a>
    </li>
  );
}

function SuiteSection({ suite }: { suite: Suite }) {
  const { data } = suite;
  const model = modelName(data.model);
  const meta = [
    model,
    runsPerCase(data.cases),
    dateText(data.dates),
    data.commit && `commit ${data.commit}`,
  ].filter(Boolean);
  return (
    <section aria-labelledby={suite.kind} className="scroll-mt-24">
      <h2
        id={suite.kind}
        className="text-2xl font-extrabold tracking-tight text-balance sm:text-3xl"
      >
        {suite.question}
      </h2>
      <p className="mt-4 max-w-3xl text-lg leading-relaxed">{suite.answer(data.cases)}</p>
      <p className="mt-3 text-sm text-ink-2">
        {meta.join(" · ")} ·{" "}
        <a href={blob(suite.report)} className={LINK}>
          report
        </a>
      </p>
      <div className="mt-6 rounded-card border border-line bg-surface p-5 sm:p-6">
        <SuiteGrid
          suite={suite.kind}
          suiteName={suite.name}
          model={model ?? "no chat model"}
          cases={data.cases}
          words={{ verdicts: suite.verdicts, groups: suite.groups ?? {} }}
        />
      </div>
      <div className="mt-4">
        <DotLegend
          pass={suite.legend.pass}
          fail={suite.legend.fail}
          unmet={tally(data.cases).unmet ? suite.legend.unmet : undefined}
        />
      </div>
    </section>
  );
}

function Measured() {
  const dates = new Set(SUITES.flatMap((suite) => suite.data.dates));
  const single = SUITES.every((suite) => suite.data.cases.every((c) => c.runs.length === 1));
  const limits = [
    dates.size === 1
      ? `Every suite ran on ${longDate([...dates][0])}.`
      : "The suites ran on different days, each on the code of its day, as its section says. Numbers from different days do not add up into one score.",
    single
      ? "One run per case. A model answers differently from run to run, so a miss here is a signal, not a rate."
      : "A model answers differently from run to run: a case's runs sit side by side, and the cards above count the cases whose runs disagreed.",
    "The faults and the attacks run on a small demo shop; loop back, look and look-alike names on a host held in memory; talk, recovery and docs search on fixed text with no host. Real services, with noisier logs, may show more misses.",
    "No run changed a real host. The faults stop at the plan, before any approval, and the loop-back cases run their approved fix on a host held in memory.",
  ];
  return (
    <section aria-labelledby="measured">
      <h2 id="measured" className="text-2xl font-extrabold tracking-tight sm:text-3xl">
        How this was measured
      </h2>
      <p className="mt-4 max-w-3xl text-lg leading-relaxed">
        Each dot is one run of Bleep&apos;s own evals: Bleep&apos;s own code, a real model and a
        case with a known right answer, scored by code wherever a rule can say it. The numbers come
        from the raw files, and a test fails when a number here differs from them.
      </p>
      <ul className="mt-8 grid gap-4 md:grid-cols-2">
        {SUITES.map((suite) => {
          const mixed = mixedCases(suite.data.cases);
          const several = suite.data.cases.some((c) => c.runs.length > 1);
          return (
            <li key={suite.kind} className="rounded-card border border-line bg-surface p-5">
              <h3 className="font-extrabold">{suite.name}</h3>
              <dl className="mt-3 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
                <dt className="text-ink-2">Model</dt>
                <dd>{modelName(suite.data.model) ?? "none"}</dd>
                <dt className="text-ink-2">Runs</dt>
                <dd>
                  {tally(suite.data.cases).runs}, {runsPerCase(suite.data.cases)}
                </dd>
                <dt className="text-ink-2">Disagreed</dt>
                <dd>
                  {several
                    ? `${mixed.length} of ${suite.data.cases.length} cases${mixed.length ? `: ${mixed.join(", ")}` : ""}`
                    : "one run each, so no spread to show"}
                </dd>
                <dt className="text-ink-2">Scored</dt>
                <dd>{judgeText(suite)}</dd>
                <dt className="text-ink-2">When</dt>
                <dd>
                  {dateText(suite.data.dates)}
                  {suite.data.commit && `, commit ${suite.data.commit}`}
                </dd>
                <dt className="text-ink-2">Sources</dt>
                <dd className="[overflow-wrap:anywhere]">
                  <a href={blob(suite.report)} className={LINK}>
                    report
                  </a>
                  {suite.data.batches.map((batch) => (
                    <span key={batch}>
                      ,{" "}
                      <a href={blob(`docs/evals/data/${batch}.jsonl`)} className={LINK}>
                        {batch}
                      </a>
                    </span>
                  ))}
                </dd>
              </dl>
            </li>
          );
        })}
      </ul>
      <h3 className="mt-12 text-xl font-extrabold">Read it with these limits</h3>
      <ul className="mt-4 flex max-w-3xl flex-col gap-4">
        {limits.map((limit) => (
          <li key={limit} className="border-l-4 border-warn pl-4 leading-relaxed text-ink-2">
            {limit}
          </li>
        ))}
      </ul>
      <p className="mt-10 max-w-2xl leading-relaxed text-ink-2">
        Every dated report and its raw runs are in{" "}
        <a href={`${REPO}/tree/main/docs/evals`} className={LINK}>
          docs/evals
        </a>
        , and how the evals work is in their{" "}
        <a href={blob("backend/evals/README.md")} className={LINK}>
          README
        </a>
        .
      </p>
    </section>
  );
}

function WhichModel() {
  const scenarios = Object.keys(round.models[0].faults).length;
  const attacks = Object.keys(round.models[0].attacks).length;
  const runs = round.models[0].runs;
  const limits = [
    `One run per case. A score like 5 of ${scenarios} moves by a whole case on a single run, so a miss is a signal, not a rate.`,
    `${capitalised(inWords(runs))} cases, all on a small demo shop. Faults on real services, with noisier logs, may separate the models differently.`,
    "A Claude model judged the root causes, for every model, including the two Claude models. The plans and the attacks are scored by code.",
    `Measured on ${longDate(round.date)}. Models and their prices change; the local models' times depend on the one PC they ran on.`,
  ];
  return (
    <section aria-labelledby="models">
      <h2 id="models" className="text-2xl font-extrabold tracking-tight sm:text-3xl">
        Which model?
      </h2>
      <p className="mt-4 max-w-3xl text-lg leading-relaxed">
        Each of {inWords(round.models.length)} models ran the same {inWords(runs)} cases once on
        Bleep&apos;s demo shop: {inWords(scenarios)} faults to find and fix, {inWords(attacks)}{" "}
        attacks that plant orders in logs and docs, and one control. Measured on{" "}
        {longDate(round.date)}, Bleep at commit {commitText(round)}, root causes judged by{" "}
        {round.judge}.
      </p>
      <div className="mt-8">
        <Results models={round.models} />
      </div>
      <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <Legend />
        <p className="max-w-md text-sm text-ink-2">
          Seconds count from the incident opening to the diagnosis. Cost is the model&apos;s bill
          for one run; a local model costs nothing but its hardware.
        </p>
      </div>
      <div className="mt-12 grid gap-12 lg:grid-cols-2 lg:gap-16">
        <div>
          <h3 className="text-xl font-extrabold">What it shows</h3>
          <ul className="mt-4 flex flex-col gap-4">
            {round.findings.map((finding) => (
              <li key={finding} className="border-l-4 border-blob pl-4 leading-relaxed">
                {finding}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3 className="text-xl font-extrabold">Read the round with these limits</h3>
          <ul className="mt-4 flex flex-col gap-4">
            {limits.map((limit) => (
              <li key={limit} className="border-l-4 border-warn pl-4 leading-relaxed text-ink-2">
                {limit}
              </li>
            ))}
          </ul>
        </div>
      </div>
      <p className="mt-10 max-w-2xl leading-relaxed text-ink-2">
        The round&apos;s{" "}
        <a href={blob(round.report)} className={LINK}>
          full report
        </a>{" "}
        reads every miss, and the{" "}
        <a href={blob(round.decision)} className={LINK}>
          chat model decision
        </a>{" "}
        says which model Bleep recommends, and why.
      </p>
    </section>
  );
}

export default function BenchmarksPage() {
  return (
    <RunDialogProvider>
      <SiteHeader page="benchmarks" />
      <main className="mx-auto w-full max-w-6xl px-4 pt-10 pb-20 sm:px-6 lg:pt-16">
        <h1 className="max-w-3xl text-[2.5rem] leading-[1.05] font-black tracking-[-0.03em] text-balance sm:text-6xl">
          What Bleep is tested on
        </h1>
        <p className="mt-6 max-w-3xl text-lg leading-relaxed text-ink-2 sm:text-xl">
          {totalRuns} runs of Bleep&apos;s own evals, each a case with a known right answer. Every
          dot below is one run: open it to read what Bleep found, what it proposed, why, and the
          tools it called.
        </p>

        <section aria-labelledby="tested" className="mt-12 sm:mt-16">
          <h2 id="tested" className="text-2xl font-extrabold tracking-tight sm:text-3xl">
            What I tested
          </h2>
          <ul className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {SUITES.map((suite) => (
              <SuiteCard key={suite.kind} suite={suite} />
            ))}
          </ul>
        </section>

        <div className="mt-20 flex flex-col gap-20 sm:mt-24 sm:gap-24">
          {SUITES.map((suite) => (
            <SuiteSection key={suite.kind} suite={suite} />
          ))}
          <WhichModel />
          <Measured />
        </div>
      </main>
      <SiteFooter />
    </RunDialogProvider>
  );
}
