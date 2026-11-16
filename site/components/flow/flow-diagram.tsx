"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { Bleep } from "@/components/bleep/bleep";

import {
  DWELL,
  GRAPH_NODES,
  SOURCES,
  STEP_ORDER,
  STEPS,
  branches,
  edgesFrom,
  type StepId,
} from "./flow";
import {
  BOX,
  DRAWN_EDGES,
  GRAPH_AREA,
  HEIGHT,
  SOURCE_TAGS,
  WIDTH,
  arrowHead,
  boxTop,
  type DrawnEdge,
} from "./geometry";

const REPO_FILE = "https://github.com/DannyKuk/bleep/blob/main/";
const FIRST_GRAPH_STEP = STEP_ORDER.indexOf(GRAPH_NODES[0]);
const PANEL_ID = "flow-step";

const nextStep = (step: StepId) => STEP_ORDER[(STEP_ORDER.indexOf(step) + 1) % STEP_ORDER.length]!;

/** Starts once half of the diagram is on screen, so the walk never plays unseen. */
function useSeen<T extends Element>() {
  const ref = useRef<T>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const node = ref.current;
    if (!node || typeof IntersectionObserver === "undefined") {
      setSeen(true);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          setSeen(true);
          observer.disconnect();
        }
      },
      { threshold: 0.25 },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return [ref, seen] as const;
}

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

/** With reduced motion the walk waits for a press of its button instead of starting itself. */
function useReducedMotion(): boolean {
  return useSyncExternalStore(
    (changed) => {
      const query = window.matchMedia?.(REDUCED_MOTION);
      query?.addEventListener("change", changed);
      return () => query?.removeEventListener("change", changed);
    },
    () => window.matchMedia?.(REDUCED_MOTION).matches ?? false,
    () => false,
  );
}

/**
 * One incident's way through Bleep, drawn from the model in `flow.ts`. A wide screen gets the
 * diagram with the chosen step beside it; a narrower one gets the same steps as a list, each with
 * its whole text and where it may lead. Bleep walks through the steps until you pick one.
 */
export function FlowDiagram() {
  const [active, setActive] = useState<StepId>(STEP_ORDER[0]!);
  const [choice, setChoice] = useState<"auto" | "playing" | "paused">("auto");
  const [stage, seen] = useSeen<HTMLDivElement>();
  const reduced = useReducedMotion();
  const playing = choice === "auto" ? !reduced : choice === "playing";

  useEffect(() => {
    if (!seen || !playing) return;
    const timer = window.setTimeout(() => setActive(nextStep), DWELL[active]);
    return () => window.clearTimeout(timer);
  }, [seen, playing, active]);

  const pick = (step: StepId) => {
    setChoice("paused");
    setActive(step);
  };

  const toggle = (
    <button
      type="button"
      onClick={() => setChoice(playing ? "paused" : "playing")}
      className="inline-flex h-10 items-center rounded-full border-2 border-line px-4 text-sm font-bold hover:bg-surface"
    >
      {playing ? "Pause the walk" : "Walk me through it"}
    </button>
  );

  return (
    <div ref={stage}>
      <div className="hidden xl:grid xl:grid-cols-[700px_1fr] xl:gap-12">
        <Wide active={active} onPick={pick} />
        <aside className="self-start xl:sticky xl:top-24">
          <Panel step={active} />
          <div className="mt-6">{toggle}</div>
        </aside>
      </div>
      <div className="max-w-2xl xl:sr-only">
        <div className="mb-8 xl:hidden">{toggle}</div>
        <Narrow active={active} />
      </div>
    </div>
  );
}

function Wide({ active, onPick }: { active: StepId; onPick: (step: StepId) => void }) {
  const lit = new Set(edgesFrom(active).map((edge) => `${edge.from}-${edge.to}`));
  return (
    <div className="relative" style={{ width: WIDTH, height: HEIGHT }}>
      <svg
        aria-hidden
        width={WIDTH}
        height={HEIGHT}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="absolute inset-0 overflow-visible"
      >
        <rect
          x={GRAPH_AREA.x}
          y={GRAPH_AREA.y}
          width={GRAPH_AREA.width}
          height={GRAPH_AREA.height}
          rx={28}
          className="fill-soft/60"
        />
        <text
          x={GRAPH_AREA.labelX}
          y={GRAPH_AREA.labelY}
          className="fill-ink-2 text-[13px] font-bold"
        >
          The incident graph
        </text>
        {SOURCE_TAGS.map((tag) => (
          <g key={tag.text}>
            <path d={tag.path} className="fill-none stroke-line" strokeWidth={2} />
            <rect
              x={tag.x}
              y={tag.y}
              width={tag.width}
              height={tag.height}
              rx={15}
              className="fill-surface stroke-line"
              strokeWidth={1.5}
            />
            <text
              x={tag.x + tag.width / 2}
              y={tag.y + 19.5}
              textAnchor="middle"
              className="fill-ink-2 text-[12px] font-semibold"
            >
              {tag.text}
            </text>
          </g>
        ))}
        {DRAWN_EDGES.map((drawn) => (
          <EdgeLine key={drawn.id} drawn={drawn} lit={lit.has(drawn.id)} />
        ))}
      </svg>
      {STEP_ORDER.map((id, index) => (
        <StepBox key={id} id={id} index={index} active={id === active} onPick={onPick} />
      ))}
      <div
        aria-hidden
        className="flow-walker pointer-events-none absolute"
        style={{
          left: BOX.x + BOX.width - 58,
          top: 0,
          transform: `translateY(${boxTop(active) + (BOX.height - 52) / 2}px)`,
        }}
      >
        <Bleep mood={STEPS[active].mood} size={52} />
      </div>
    </div>
  );
}

function EdgeLine({ drawn, lit }: { drawn: DrawnEdge; lit: boolean }) {
  const { kind } = drawn.edge;
  const human = drawn.edge.from === "approval";
  /** The way the run leaves the chosen step, and every answer of yours, are drawn in accent. */
  const accent = lit || human;
  return (
    <g data-edge={drawn.id} className={lit ? "" : "opacity-80"}>
      <path
        d={drawn.path}
        className={`fill-none ${accent ? "stroke-accent" : "stroke-ink-2"} transition-[stroke]`}
        strokeWidth={lit ? 2.5 : 1.75}
        strokeDasharray={kind === "back" ? "5 5" : undefined}
      />
      <polygon points={arrowHead(drawn.head)} className={accent ? "fill-accent" : "fill-ink-2"} />
      {drawn.tag && (
        <g>
          <rect
            x={drawn.tag.x}
            y={drawn.tag.y}
            width={drawn.tag.width}
            height={drawn.tag.height}
            rx={12}
            className="fill-surface stroke-line"
            strokeWidth={1.5}
          />
          <text
            x={drawn.tag.x + drawn.tag.width / 2}
            y={drawn.tag.y + 16.5}
            textAnchor="middle"
            className="fill-ink text-[12px] font-bold"
          >
            Report
          </text>
        </g>
      )}
      {drawn.label && (
        <text
          x={drawn.label.x}
          y={drawn.label.y}
          textAnchor={drawn.label.anchor}
          className={`text-[12px] font-bold ${accent ? "fill-accent" : "fill-ink-2"}`}
        >
          {drawn.label.text}
        </text>
      )}
    </g>
  );
}

function StepBox({
  id,
  index,
  active,
  onPick,
}: {
  id: StepId;
  index: number;
  active: boolean;
  onPick: (step: StepId) => void;
}) {
  const step = STEPS[id];
  const human = id === "approval";
  return (
    <button
      type="button"
      aria-pressed={active}
      aria-controls={PANEL_ID}
      aria-label={`${index + 1}. ${step.title}`}
      onClick={() => onPick(id)}
      onMouseEnter={() => onPick(id)}
      onFocus={() => onPick(id)}
      className={`absolute flex flex-col justify-center rounded-row border-2 py-2 pr-16 pl-4 text-left transition-[border-color,box-shadow,background-color] ${
        active
          ? "border-accent bg-surface shadow-[0_10px_30px_-16px_rgb(40_30_120/0.45)]"
          : human
            ? "border-accent/50 bg-surface hover:border-accent"
            : "border-line bg-surface hover:border-ink-2/40"
      }`}
      style={{ left: BOX.x, top: boxTop(id), width: BOX.width, height: BOX.height }}
    >
      <span className="flex items-baseline gap-2">
        <span className="font-mono text-xs text-ink-2">{index + 1}</span>
        <span className={`font-extrabold ${human ? "text-accent" : ""}`}>{step.title}</span>
      </span>
      <span className="mt-0.5 text-[13px] leading-snug text-ink-2">{step.short}</span>
    </button>
  );
}

function Panel({ step: id }: { step: StepId }) {
  const step = STEPS[id];
  const index = STEP_ORDER.indexOf(id);
  const ways = branches(id);
  const next = edgesFrom(id).find((edge) => edge.kind === "next" && !edge.when);
  return (
    <div
      id={PANEL_ID}
      aria-live="polite"
      className="rounded-card border border-line bg-surface p-7"
    >
      <div className="flex items-end gap-4">
        <Bleep mood={step.mood} size={88} />
        <p className="pb-2 font-mono text-sm text-ink-2">
          Step {index + 1} of {STEP_ORDER.length}
          {index >= FIRST_GRAPH_STEP ? ", in the graph" : ""}
        </p>
      </div>
      <h2
        className={`mt-5 text-2xl font-extrabold tracking-tight ${id === "approval" ? "text-accent" : ""}`}
      >
        {step.title}
      </h2>
      <p className="mt-3 text-[17px] leading-relaxed text-ink-2">{step.text}</p>
      <Ways next={next ? STEPS[next.to].title : undefined} ways={ways} />
      <a
        href={`${REPO_FILE}${step.code}`}
        className="mt-6 inline-block font-mono text-[13px] text-accent underline-offset-4 hover:underline"
      >
        {step.code.replace("backend/src/bleep/", "")}
      </a>
    </div>
  );
}

function Ways({ next, ways }: { next?: string; ways: string[] }) {
  if (!next && ways.length === 0) return <p className="mt-5 font-bold">The run ends here.</p>;
  return (
    <ul className="mt-5 flex flex-wrap gap-2">
      {next && <WayTag text={`Then: ${next}`} />}
      {ways.map((way) => (
        <WayTag key={way} text={way} />
      ))}
    </ul>
  );
}

function WayTag({ text }: { text: string }) {
  return (
    <li className="rounded-full bg-soft px-3 py-1.5 text-sm font-semibold text-ink-2">{text}</li>
  );
}

function Narrow({ active }: { active: StepId }) {
  return (
    <>
      <p className="mb-3 font-bold text-ink-2">What the watcher reads</p>
      <ul className="mb-4 flex flex-wrap gap-2">
        {SOURCES.map((source) => (
          <li
            key={source}
            className="rounded-full border border-line bg-surface px-3 py-1.5 text-sm font-semibold text-ink-2"
          >
            {source}
          </li>
        ))}
      </ul>
      <ol className="flex flex-col">
        {STEP_ORDER.map((id, index) => (
          <NarrowStep key={id} id={id} index={index} active={id === active} />
        ))}
      </ol>
    </>
  );
}

function NarrowStep({ id, index, active }: { id: StepId; index: number; active: boolean }) {
  const step = STEPS[id];
  const ways = branches(id);
  const next = edgesFrom(id).find((edge) => edge.kind === "next" && !edge.when);
  const human = id === "approval";
  const graphStart = index === FIRST_GRAPH_STEP;
  return (
    <li className="flex flex-col">
      {graphStart && (
        <p className="mt-4 mb-1 border-t border-line pt-6 text-sm font-bold text-ink-2">
          The incident graph
        </p>
      )}
      {index > 0 && <span aria-hidden className="mx-auto h-6 w-0.5 rounded-full bg-line" />}
      <div
        className={`relative rounded-row border-2 bg-surface p-5 pr-16 transition-[border-color] ${
          active ? "border-accent" : human ? "border-accent/50" : "border-line"
        }`}
      >
        <h3 className="flex items-baseline gap-2">
          <span className="font-mono text-xs text-ink-2">{index + 1}</span>
          <span className={`text-lg font-extrabold ${human ? "text-accent" : ""}`}>
            {step.title}
          </span>
        </h3>
        <p className="mt-2 leading-relaxed text-ink-2">{step.text}</p>
        <Ways next={next ? STEPS[next.to].title : undefined} ways={ways} />
        {active && <Bleep mood={step.mood} size={44} className="absolute top-4 right-4" />}
      </div>
    </li>
  );
}
