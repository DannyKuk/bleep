import { Bleep, type Mood } from "@/components/bleep/bleep";

import { Replay } from "~/components/replay/replay";

const REPO = "https://github.com/DannyKuk/bleep";
const blob = (path: string) => `${REPO}/blob/main/${path}`;
const VIDEO = "https://github.com/user-attachments/assets/a3fd906c-4b35-498e-bbbb-c9f7c85a612a";

const STEPS: { mood: Mood; title: string; text: string }[] = [
  {
    mood: "watching",
    title: "Notices",
    text: "It reads each host's Docker events over SSH every 15 seconds and checks your URLs from outside. A failure that holds opens an incident.",
  },
  {
    mood: "working",
    title: "Investigates",
    text: "It reads logs, events and the host the way you would, with read-only tools, and names the cause.",
  },
  {
    mood: "needs_you",
    title: "Asks",
    text: "It proposes a plan of named steps, like start shop-db, and waits. You say yes or no on the dashboard or in Telegram.",
  },
  {
    mood: "working",
    title: "Fixes",
    text: "Only the steps you approved run, and only from an allowlist of Docker actions.",
  },
  {
    mood: "all_good",
    title: "Checks",
    text: "It calls nothing fixed until the containers are healthy and the URLs answer again. Then it writes a short report.",
  },
];

const NEVER: { title: string; text: string }[] = [
  {
    title: "Changes a host without your yes",
    text: "Every write waits for an explicit approval of that plan. Nothing is approved automatically.",
  },
  {
    title: "Runs a shell it was not given",
    text: "Commands come from an allowlist. There is no generic shell with write access.",
  },
  {
    title: "Takes orders from a log line",
    text: "Tool output is capped, redacted and marked as untrusted, so text in a log cannot steer it.",
  },
  {
    title: "Shows its keys to the model",
    text: "SSH keys and tokens never reach the model, and never sit in the database.",
  },
  {
    title: "Touches itself",
    text: "Bleep's own containers never open an incident and are never the target of a fix.",
  },
  {
    title: "Says fixed without checking",
    text: "A fix counts only once the service is healthy and its URL answers.",
  },
];

const RESULTS: { figure: string; text: string; report: string }[] = [
  {
    figure: "23 of 27",
    text: "root causes right on the demo shop's faults, three runs each with a hosted model, in about a minute and for $0.0028 a run.",
    report: "docs/evals/scenarios-2026-11-05-3.md",
  },
  {
    figure: "20 of 20",
    text: "prompt-injection attacks through logs, replies, docs, commits and memory failed to steer Bleep or leak a secret.",
    report: "docs/evals/injection-2026-11-05-2.md",
  },
  {
    figure: "7 of 8",
    text: "faults right with a local 20B model served by LM Studio, at $0 a run.",
    report: "docs/evals/scenarios-local-2026-11-03.md",
  },
];

const STACK = [
  "Python",
  "FastAPI",
  "LangGraph",
  "SQLite",
  "Next.js 16",
  "Docker over SSH",
  "Telegram",
  "Hosted or local models",
];

function Section({
  id,
  title,
  lead,
  onBand = false,
  children,
}: {
  id: string;
  title: string;
  lead?: string;
  /** On the dark safety band, where the page's muted ink is too dark to read. */
  onBand?: boolean;
  children: React.ReactNode;
}) {
  return (
    <section id={id} className="mx-auto w-full max-w-6xl scroll-mt-8 px-4 py-16 sm:px-6 sm:py-24">
      <h2 className="max-w-2xl text-3xl leading-tight font-extrabold tracking-tight text-balance sm:text-4xl">
        {title}
      </h2>
      {lead && (
        <p
          className={`mt-3 max-w-2xl text-lg leading-relaxed ${onBand ? "text-[#aba9c8]" : "text-ink-2"}`}
        >
          {lead}
        </p>
      )}
      <div className="mt-10 sm:mt-12">{children}</div>
    </section>
  );
}

export default function Page() {
  return (
    <>
      <header
        id="top"
        className="mx-auto flex w-full max-w-6xl items-center justify-between gap-4 px-4 pt-5 sm:px-6"
      >
        <a href="#top" className="flex items-center gap-2 text-lg font-extrabold">
          <Bleep mood="all_good" size={30} />
          Bleep
        </a>
        <nav aria-label="Page" className="flex items-center gap-1 text-sm font-bold text-ink-2">
          <a href="#how" className="hidden rounded-full px-3 py-2 hover:bg-soft sm:block">
            How it works
          </a>
          <a href="#safety" className="hidden rounded-full px-3 py-2 hover:bg-soft sm:block">
            Safety
          </a>
          <a
            href={REPO}
            className="rounded-full bg-ink px-4 py-2 text-bg transition-opacity hover:opacity-85"
          >
            GitHub
          </a>
        </nav>
      </header>

      <main>
        <section className="mx-auto grid w-full max-w-6xl items-center gap-12 px-4 pt-14 pb-16 sm:px-6 lg:grid-cols-[1fr_1.1fr] lg:gap-14 lg:pt-20">
          <div>
            <h1 className="text-[2.75rem] leading-[1.02] font-black tracking-[-0.03em] text-balance sm:text-6xl lg:text-[4.25rem]">
              The <span className="whitespace-nowrap">on-call</span> bot that asks before it fixes.
            </h1>
            <p className="mt-6 max-w-xl text-lg leading-relaxed text-ink-2 sm:text-xl">
              Bleep watches the containers on your Docker hosts. When one breaks, it finds out why
              and proposes a fix. Nothing changes until you say yes, and nothing counts as fixed
              until Bleep has checked.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <a
                href={REPO}
                className="inline-flex h-12 items-center rounded-full bg-accent px-6 font-bold text-accent-ink transition-transform hover:-translate-y-0.5"
              >
                Read the code on GitHub
              </a>
              <a
                href={blob("docs/setup.md")}
                className="inline-flex h-12 items-center rounded-full border-2 border-line px-6 font-bold hover:bg-surface"
              >
                Set it up
              </a>
            </div>
            <p className="mt-8 max-w-md text-sm leading-relaxed text-ink-2">
              The replay is a real incident from Bleep&apos;s tests, shortened. Bleep waits for your
              yes, as it does on a real host.
            </p>
          </div>
          <div className="relative isolate">
            <div
              aria-hidden
              className="absolute -inset-4 -z-10 rounded-[56px] bg-blob/40 blur-3xl sm:-inset-8"
            />
            <Replay />
          </div>
        </section>

        <Section
          id="how"
          title="One incident, start to end"
          lead="The same five steps every time. You are in the middle of them."
        >
          <ol className="grid gap-x-6 gap-y-10 sm:grid-cols-2 lg:grid-cols-5">
            {STEPS.map((item, index) => (
              <li key={item.title} className="flex flex-col">
                <div className="flex items-end gap-3">
                  <Bleep mood={item.mood} size={64} />
                  <span className="pb-1 font-mono text-sm text-ink-2">{index + 1}</span>
                </div>
                <h3
                  className={`mt-4 text-xl font-extrabold ${item.mood === "needs_you" ? "text-accent" : ""}`}
                >
                  {item.title}
                </h3>
                <p className="mt-2 leading-relaxed text-ink-2">{item.text}</p>
              </li>
            ))}
          </ol>
        </Section>

        <div className="bg-band text-[#eeedf9]">
          <Section
            id="safety"
            onBand
            title="What Bleep never does"
            lead="These rules live in the code, and the tests prove them."
          >
            <ul className="grid gap-x-10 gap-y-8 sm:grid-cols-2 lg:grid-cols-3">
              {NEVER.map((rule) => (
                <li key={rule.title} className="border-t border-white/15 pt-5">
                  <h3 className="flex items-start gap-3 text-lg font-extrabold">
                    <span
                      aria-hidden
                      className="mt-2.5 size-2.5 shrink-0 rounded-full bg-[#7CF2B8]"
                    />
                    {rule.title}
                  </h3>
                  <p className="mt-2 pl-5.5 leading-relaxed text-[#aba9c8]">{rule.text}</p>
                </li>
              ))}
            </ul>
          </Section>
        </div>

        <Section
          id="results"
          title="Measured, not promised"
          lead="Every number comes from a dated report in the repository."
        >
          <ul className="divide-y divide-line border-y border-line">
            {RESULTS.map((result) => (
              <li
                key={result.report}
                className="grid gap-2 py-6 sm:grid-cols-[12rem_1fr_auto] sm:items-baseline sm:gap-8"
              >
                <p className="text-4xl font-black tracking-tight">{result.figure}</p>
                <p className="max-w-2xl text-lg leading-relaxed text-ink-2">{result.text}</p>
                <a
                  href={blob(result.report)}
                  className="font-bold text-accent underline-offset-4 hover:underline"
                >
                  Read the report
                </a>
              </li>
            ))}
          </ul>
        </Section>

        <Section
          id="video"
          title="Watch one real run"
          lead="65 seconds of a real incident on the demo shop, from the broken container to the report."
        >
          <video
            src={VIDEO}
            controls
            playsInline
            preload="metadata"
            className="aspect-video w-full rounded-card border border-line bg-screen"
          >
            <a href={REPO}>Watch the video on GitHub.</a>
          </video>
        </Section>

        <Section id="stack" title="Built with">
          <ul className="flex flex-wrap gap-2.5">
            {STACK.map((item) => (
              <li
                key={item}
                className="rounded-full border border-line bg-surface px-4 py-2 font-semibold"
              >
                {item}
              </li>
            ))}
          </ul>
        </Section>
      </main>

      <footer className="border-t border-line">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-10 text-sm text-ink-2 sm:flex-row sm:items-center sm:justify-between sm:px-6">
          <div className="flex items-center gap-3">
            <Bleep mood="resting" size={44} />
            <p>
              Built by{" "}
              <a href="https://dkwebsite.de" className="font-bold text-ink hover:underline">
                Danny Kukic
              </a>
              . Open source under the MIT licence.
            </p>
          </div>
          <nav aria-label="Footer" className="flex flex-wrap gap-x-5 gap-y-2 font-semibold">
            <a href={REPO} className="hover:text-ink">
              GitHub
            </a>
            <a href={blob("docs/architecture.md")} className="hover:text-ink">
              Architecture
            </a>
            <a href="https://dkwebsite.de/imprint" className="hover:text-ink">
              Legal notice
            </a>
            <a href="https://dkwebsite.de/privacy" className="hover:text-ink">
              Privacy
            </a>
          </nav>
        </div>
      </footer>
    </>
  );
}
