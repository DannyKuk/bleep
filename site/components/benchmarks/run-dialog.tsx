"use client";

import { type ReactNode, createContext, useCallback, useContext, useRef, useState } from "react";

import type {
  AgentRun,
  Check,
  Details,
  LineRun,
  Outcome,
  RunDetail,
  SearchRun,
  SuiteKind,
  TalkRun,
} from "~/lib/benchmarks/extract";

import { OutcomeDot } from "./dot";

export type DetailSet = SuiteKind | "round" | "faults-stronger" | "look-stronger";

// Each set is its own chunk, fetched when a visitor opens one of its runs, so the page itself
// carries only the dots.
const LOADERS: Record<DetailSet, () => Promise<{ default: unknown }>> = {
  faults: () => import("~/lib/benchmarks/data/faults.runs.json"),
  attacks: () => import("~/lib/benchmarks/data/attacks.runs.json"),
  talk: () => import("~/lib/benchmarks/data/talk.runs.json"),
  look: () => import("~/lib/benchmarks/data/look.runs.json"),
  loopback: () => import("~/lib/benchmarks/data/loopback.runs.json"),
  recovery: () => import("~/lib/benchmarks/data/recovery.runs.json"),
  scripted: () => import("~/lib/benchmarks/data/scripted.runs.json"),
  docs: () => import("~/lib/benchmarks/data/docs.runs.json"),
  round: () => import("~/lib/benchmarks/data/round.runs.json"),
  "faults-stronger": () => import("~/lib/benchmarks/data/faults-stronger.runs.json"),
  "look-stronger": () => import("~/lib/benchmarks/data/look-stronger.runs.json"),
};

/** A dot's run: where its detail is, and what the dot already says about it. */
export type RunRef = {
  set: DetailSet;
  key: string;
  title: string;
  subtitle: string;
  outcome: Outcome;
  verdict: string;
};

const OpenRun = createContext<(run: RunRef) => void>(() => {});

export const useOpenRun = () => useContext(OpenRun);

type Shown = { run: RunRef; detail: RunDetail | "loading" | "missing" | "failed" };

/** Holds the one dialog every dot on the page opens. */
export function RunDialogProvider({ children }: { children: ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [shown, setShown] = useState<Shown | null>(null);
  const latest = useRef<string>("");

  const open = useCallback((run: RunRef) => {
    const id = `${run.set}:${run.key}`;
    latest.current = id;
    setShown({ run, detail: "loading" });
    dialog.current?.showModal();
    LOADERS[run.set]()
      .then(({ default: details }) => (details as Details)[run.key] ?? "missing")
      .catch(() => "failed" as const)
      .then((detail) => {
        if (latest.current === id) setShown({ run, detail });
      });
  }, []);

  const close = () => dialog.current?.close();

  return (
    <OpenRun.Provider value={open}>
      {children}
      <dialog
        ref={dialog}
        aria-labelledby="run-title"
        onClick={(event) => event.target === event.currentTarget && close()}
        className="m-auto max-h-[min(88vh,56rem)] w-[min(100%-2rem,46rem)] max-w-none overflow-hidden rounded-card border border-line bg-surface p-0 text-ink shadow-2xl backdrop:bg-[#0d0c1f]/60"
      >
        {shown && (
          <div className="flex max-h-[inherit] flex-col">
            <header className="flex items-start gap-3 border-b border-line px-5 py-4 sm:px-6">
              <div className="min-w-0 flex-1">
                <h2
                  id="run-title"
                  className="font-mono text-base font-bold [overflow-wrap:anywhere] sm:text-lg"
                >
                  {shown.run.title}
                </h2>
                <p className="mt-1 text-sm text-ink-2">{shown.run.subtitle}</p>
                <p className="mt-2 flex items-center gap-2 text-sm font-bold">
                  <OutcomeDot
                    outcome={shown.run.outcome}
                    stronger={shown.run.set.endsWith("-stronger")}
                  />
                  {shown.run.verdict}
                </p>
              </div>
              <button
                type="button"
                onClick={close}
                className="shrink-0 rounded-full border border-line px-3 py-1.5 text-sm font-bold hover:bg-soft"
              >
                Close
              </button>
            </header>
            <div className="overflow-y-auto px-5 py-5 [overflow-wrap:anywhere] sm:px-6">
              <Body detail={shown.detail} />
            </div>
          </div>
        )}
      </dialog>
    </OpenRun.Provider>
  );
}

function Body({ detail }: { detail: Shown["detail"] }) {
  if (detail === "loading") return <p className="text-ink-2">Loading the run…</p>;
  if (detail === "missing") return <p className="text-ink-2">This run has no detail.</p>;
  if (detail === "failed") {
    return <p className="text-ink-2">The run could not be loaded. Try again.</p>;
  }
  switch (detail.type) {
    case "agent":
      return <Agent run={detail} />;
    case "talk":
      return <Talk run={detail} />;
    case "line":
      return <Line run={detail} />;
    case "search":
      return <Search run={detail} />;
  }
}

function Part({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-6 first:mt-0">
      <h3 className="text-xs font-extrabold tracking-wider text-ink-2 uppercase">{title}</h3>
      <div className="mt-2 leading-relaxed">{children}</div>
    </section>
  );
}

function Checks({ checks }: { checks: Check[] }) {
  if (checks.length === 0) return null;
  return (
    <Part title="Scored">
      <ul className="flex flex-col gap-1.5">
        {checks.map((check) => (
          <li key={check.label} className="flex items-start gap-2">
            <span
              role="img"
              aria-label={check.ok === false ? "No" : check.ok ? "Yes" : "Not scored"}
              className={`mt-0.5 font-black ${check.ok === false ? "text-down" : check.ok ? "text-ok" : "text-ink-2"}`}
            >
              {check.ok === false ? "✕" : check.ok ? "✓" : "–"}
            </span>
            <span>{check.label}</span>
          </li>
        ))}
      </ul>
    </Part>
  );
}

/** Why a verdict differs from the checks of the day, when the case files changed since. */
function Rescored({ note }: { note: string | null }) {
  if (!note) return null;
  return (
    <Part title="Scored again">
      <p className="text-ink-2">{note}</p>
    </Part>
  );
}

function Failure({ failure }: { failure: string | null }) {
  if (!failure) return null;
  return (
    <p className="mb-5 rounded-row bg-down-bg px-4 py-3 text-sm font-bold text-down">
      The run ended early: {failure.replaceAll("_", " ")}.
    </p>
  );
}

const percent = (confidence: number) => `${Math.round(confidence * 100)} %`;

function Agent({ run }: { run: AgentRun }) {
  return (
    <>
      <Failure failure={run.failure} />
      {run.expected && (
        <Part title="What broke">
          <p className="text-ink-2">{run.expected}</p>
        </Part>
      )}
      <Checks checks={run.checks} />
      {run.diagnosis && (
        <Part title="Diagnosis">
          <p>{run.diagnosis.rootCause}</p>
          <p className="mt-2 text-sm text-ink-2">
            Category {run.diagnosis.category}, {percent(run.diagnosis.confidence)} sure
            {run.diagnosis.unsure && ", marked unsure"}
          </p>
        </Part>
      )}
      {run.plan && (
        <Part title="Plan">
          {run.plan.steps.length > 0 ? (
            <ul className="flex flex-col gap-1">
              {run.plan.steps.map((step) => (
                <li key={step} className="font-mono text-sm">
                  {step}
                </li>
              ))}
            </ul>
          ) : (
            <p className="font-bold">No action: Bleep asks a person instead.</p>
          )}
          {run.plan.reason && (
            <p className="mt-3">
              <span className="font-bold">Reason. </span>
              {run.plan.reason}
            </p>
          )}
          {run.plan.instructions && (
            <p className="mt-3">
              <span className="font-bold">For the person. </span>
              {run.plan.instructions}
            </p>
          )}
        </Part>
      )}
      {run.note && (
        <Part title="Grader's note">
          <p className="text-ink-2">{run.note}</p>
        </Part>
      )}
      <Part title={`Tool calls (${run.toolCalls.length})`}>
        {run.toolCalls.length === 0 ? (
          <p className="text-ink-2">None.</p>
        ) : (
          <ol className="flex list-decimal flex-col gap-1 pl-6 font-mono text-sm marker:text-ink-2">
            {run.toolCalls.map((call, i) => (
              <li key={i}>
                <span className="font-bold">{call.name}</span>
                {call.args && <span className="text-ink-2"> {call.args}</span>}
              </li>
            ))}
          </ol>
        )}
      </Part>
    </>
  );
}

function Talk({ run }: { run: TalkRun }) {
  return (
    <>
      <Failure failure={run.failure} />
      <Part title="The person wrote">
        <p className="font-bold">{run.message}</p>
      </Part>
      <Part title="Bleep read it as">
        <p>
          {run.readAs}
          {run.action && <span className="font-mono text-sm text-ink-2"> ({run.action})</span>}
        </p>
      </Part>
      {run.answer && (
        <Part title="Bleep answered">
          <p>{run.answer}</p>
        </Part>
      )}
      <Checks checks={run.checks} />
      <Rescored note={run.note} />
    </>
  );
}

function Line({ run }: { run: LineRun }) {
  return (
    <>
      <Failure failure={run.failure} />
      <Part title="The line Bleep wrote">
        <p className="font-bold">{run.line ?? "None."}</p>
      </Part>
      <Checks checks={run.checks} />
      <Rescored note={run.note} />
    </>
  );
}

function Search({ run }: { run: SearchRun }) {
  return (
    <>
      <Part title="Question">
        <p className="font-bold">{run.question}</p>
      </Part>
      <Part title="Pages it found, best first">
        <ol className="flex list-decimal flex-col gap-1 pl-6 font-mono text-sm marker:text-ink-2">
          {run.found.map((page, i) => (
            <li key={`${page}-${i}`} className={page === run.page ? "font-bold text-ok" : ""}>
              {page}
              {page === run.page && <span className="font-sans"> (the page that answers it)</span>}
            </li>
          ))}
        </ol>
        {run.rank === null && (
          <p className="mt-2 text-down">
            The page that answers it, <span className="font-mono">{run.page}</span>, was not among
            them.
          </p>
        )}
      </Part>
    </>
  );
}
