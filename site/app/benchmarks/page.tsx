import type { Metadata } from "next";

import { Legend, Results } from "~/components/benchmarks/results";
import { REPO, SiteFooter, SiteHeader, blob } from "~/components/chrome";
import { type Round, inWords, longDate, round } from "~/lib/benchmarks/round";

export const metadata: Metadata = {
  title: `How Bleep does on ${inWords(round.models.length)} models, Bleep`,
  description: `The same ${inWords(round.models[0].runs)} cases on ${inWords(round.models.length)} models: what Bleep finds, what it proposes, whether planted text steers it, and what a run costs.`,
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

export default function BenchmarksPage() {
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
    <>
      <SiteHeader page="benchmarks" />
      <main className="mx-auto w-full max-w-6xl px-4 pt-10 pb-20 sm:px-6 lg:pt-16">
        <h1 className="max-w-3xl text-[2.5rem] leading-[1.05] font-black tracking-[-0.03em] text-balance sm:text-6xl">
          How Bleep does on {inWords(round.models.length)} models
        </h1>
        <p className="mt-6 max-w-3xl text-lg leading-relaxed text-ink-2 sm:text-xl">
          Each model ran the same {inWords(runs)} cases once on Bleep&apos;s demo shop:{" "}
          {inWords(scenarios)} faults to find and fix, {inWords(attacks)} attacks that plant orders
          in logs and docs, and one control. Measured on {longDate(round.date)}, Bleep at commit{" "}
          {commitText(round)}, root causes judged by {round.judge}.
        </p>

        <section aria-labelledby="results" className="mt-12 sm:mt-16">
          <h2 id="results" className="sr-only">
            Results
          </h2>
          <Results models={round.models} />
          <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <Legend />
            <p className="max-w-md text-sm text-ink-2">
              Seconds count from the incident opening to the diagnosis. Cost is the model&apos;s
              bill for one run; a local model costs nothing but its hardware.
            </p>
          </div>
        </section>

        <div className="mt-16 grid gap-12 sm:mt-20 lg:grid-cols-2 lg:gap-16">
          <section aria-labelledby="limits">
            <h2 id="limits" className="text-2xl font-extrabold tracking-tight sm:text-3xl">
              Read it with these limits
            </h2>
            <ul className="mt-6 flex flex-col gap-4">
              {limits.map((limit) => (
                <li key={limit} className="border-l-4 border-warn pl-4 leading-relaxed text-ink-2">
                  {limit}
                </li>
              ))}
            </ul>
          </section>
          <section aria-labelledby="shows">
            <h2 id="shows" className="text-2xl font-extrabold tracking-tight sm:text-3xl">
              What it shows
            </h2>
            <ul className="mt-6 flex flex-col gap-4">
              {round.findings.map((finding) => (
                <li key={finding} className="border-l-4 border-blob pl-4 leading-relaxed">
                  {finding}
                </li>
              ))}
            </ul>
          </section>
        </div>

        <p className="mt-16 max-w-2xl leading-relaxed text-ink-2">
          Every run&apos;s diagnosis, plan and tool calls are in the{" "}
          <a href={blob(round.report)} className={LINK}>
            full report
          </a>
          , with the{" "}
          <a href={`${REPO}/tree/main/docs/evals/data`} className={LINK}>
            raw data
          </a>{" "}
          behind every number here. Which model Bleep recommends, and why, is in the{" "}
          <a href={blob(round.decision)} className={LINK}>
            chat model decision
          </a>
          .
        </p>
      </main>
      <SiteFooter />
    </>
  );
}
