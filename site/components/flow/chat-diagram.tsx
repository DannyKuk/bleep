"use client";

import { useEffect, useRef, useState } from "react";

import { Bleep } from "@/components/bleep/bleep";
import { blob } from "~/components/chrome";

import {
  CHAT_DWELL,
  CHAT_ENDS,
  CHAT_ORDER,
  CHAT_SOURCES,
  CHAT_STEPS,
  INTO_INCIDENT,
  chatBranches,
  chatEdgesFrom,
  type ChatStepId,
} from "./chat";
import {
  CHAT_HEIGHT,
  CHAT_SOURCE_TAGS,
  DRAWN_CHAT_EDGES,
  chatBoxTop,
  type DrawnChatEdge,
} from "./chat-geometry";
import { Ways, follow } from "./flow-diagram";
import { BOX, WIDTH, arrowHead } from "./geometry";

const PANEL_ID = "chat-step";
const HUMAN: ChatStepId = "offer";
const LAST_STEP = CHAT_ORDER.at(-1)!;
const nextStep = (step: ChatStepId) => CHAT_ORDER[CHAT_ORDER.indexOf(step) + 1] ?? LAST_STEP;
const INCIDENT_EDGE = DRAWN_CHAT_EDGES.find((drawn) => drawn.edge.to === INTO_INCIDENT)!;
const leadsIntoIncident = (step: ChatStepId) =>
  chatEdgesFrom(step).some((edge) => edge.to === INTO_INCIDENT);

const thenOf = (step: ChatStepId) => {
  const next = chatEdgesFrom(step).find((edge) => edge.kind === "next" && !edge.when);
  return next?.kind === "next" ? CHAT_STEPS[next.to].title : undefined;
};

/**
 * A chat message's way through Bleep, drawn from the model in `chat.ts` in the incident
 * diagram's look: one way down, and a labelled way out to the side where the chat stops. The
 * way into the incident loop is a button that switches the page to that diagram.
 *
 * As there, nothing moves until the visitor asks for the walk, and picking a step stops it.
 */
export function ChatDiagram({ onIncident }: { onIncident: () => void }) {
  const [active, setActive] = useState<ChatStepId>(CHAT_ORDER[0]);
  const [walking, setWalking] = useState(false);
  const stage = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!walking) return;
    follow(stage.current, active);
    const timer = window.setTimeout(() => {
      if (active === LAST_STEP) setWalking(false);
      else setActive(nextStep);
    }, CHAT_DWELL[active]);
    return () => window.clearTimeout(timer);
  }, [walking, active]);

  const pick = (step: ChatStepId) => {
    setWalking(false);
    setActive(step);
  };

  const peek = (step: ChatStepId) => {
    if (!walking) setActive(step);
  };

  const walk = () => {
    if (!walking && active === LAST_STEP) setActive(CHAT_ORDER[0]);
    setWalking(!walking);
  };

  const toggle = (
    <button
      type="button"
      onClick={walk}
      className="inline-flex h-11 items-center rounded-full bg-accent px-5 font-bold text-accent-ink transition-transform hover:-translate-y-0.5"
    >
      {walking ? "Pause the walk" : "Walk me through it"}
    </button>
  );

  return (
    <div ref={stage}>
      <div className="hidden xl:grid xl:grid-cols-[700px_1fr] xl:gap-12">
        <Wide active={active} onPick={pick} onPeek={peek} onIncident={onIncident} />
        <aside className="self-start xl:sticky xl:top-24">
          <Panel step={active} />
          <div className="mt-6">{toggle}</div>
        </aside>
      </div>
      <div className="max-w-2xl xl:sr-only">
        <div className="mb-8 xl:hidden">{toggle}</div>
        <Narrow active={active} onIncident={onIncident} />
      </div>
    </div>
  );
}

function Wide({
  active,
  onPick,
  onPeek,
  onIncident,
}: {
  active: ChatStepId;
  onPick: (step: ChatStepId) => void;
  onPeek: (step: ChatStepId) => void;
  onIncident: () => void;
}) {
  const lit = new Set(chatEdgesFrom(active).map((edge) => `${edge.from}-${edge.to}`));
  const link = INCIDENT_EDGE.tag!;
  return (
    <div className="relative" style={{ width: WIDTH, height: CHAT_HEIGHT }}>
      <svg
        aria-hidden
        width={WIDTH}
        height={CHAT_HEIGHT}
        viewBox={`0 0 ${WIDTH} ${CHAT_HEIGHT}`}
        className="absolute inset-0 overflow-visible"
      >
        {CHAT_SOURCE_TAGS.map((tag) => (
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
        {DRAWN_CHAT_EDGES.map((drawn) => (
          <EdgeLine key={drawn.id} drawn={drawn} lit={lit.has(drawn.id)} />
        ))}
      </svg>
      {CHAT_ORDER.map((id, index) => (
        <StepBox
          key={id}
          id={id}
          index={index}
          active={id === active}
          onPick={onPick}
          onPeek={onPeek}
        />
      ))}
      <button
        type="button"
        onClick={onIncident}
        aria-label={`${CHAT_ENDS.incident.title}: show the incident diagram`}
        className="absolute flex flex-col justify-center rounded-xl border-2 border-accent/50 bg-surface px-3 text-left transition-colors hover:border-accent"
        style={{ left: link.x, top: link.y, width: link.width, height: link.height }}
      >
        <span className="text-[13px] font-extrabold text-accent">
          {CHAT_ENDS.incident.title} <span aria-hidden>→</span>
        </span>
        <span className="text-[12px] leading-tight font-semibold text-ink-2">
          {CHAT_ENDS.incident.note}
        </span>
      </button>
      <div
        aria-hidden
        className="flow-walker pointer-events-none absolute"
        style={{
          left: BOX.x + BOX.width - 58,
          top: 0,
          transform: `translateY(${chatBoxTop(active) + (BOX.height - 52) / 2}px)`,
        }}
      >
        <Bleep mood={CHAT_STEPS[active].mood} size={52} />
      </div>
    </div>
  );
}

function EdgeLine({ drawn, lit }: { drawn: DrawnChatEdge; lit: boolean }) {
  const { edge } = drawn;
  /** The way the message leaves the chosen step, and every press of yours, are drawn in accent. */
  const accent = lit || edge.from === HUMAN;
  const tag = edge.kind === "end" && edge.to !== INTO_INCIDENT ? drawn.tag : undefined;
  return (
    <g data-edge={drawn.id} className={lit ? "" : "opacity-80"}>
      <path
        d={drawn.path}
        className={`fill-none ${accent ? "stroke-accent" : "stroke-ink-2"} transition-[stroke]`}
        strokeWidth={lit ? 2.5 : 1.75}
      />
      <polygon points={arrowHead(drawn.head)} className={accent ? "fill-accent" : "fill-ink-2"} />
      {tag && edge.kind === "end" && (
        <g>
          <rect
            x={tag.x}
            y={tag.y}
            width={tag.width}
            height={tag.height}
            rx={12}
            className="fill-surface stroke-line"
            strokeWidth={1.5}
          />
          <text x={tag.x + 12} y={tag.y + 17.5} className="fill-ink text-[12px] font-bold">
            {CHAT_ENDS[edge.to].title}
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
  onPeek,
}: {
  id: ChatStepId;
  index: number;
  active: boolean;
  onPick: (step: ChatStepId) => void;
  onPeek: (step: ChatStepId) => void;
}) {
  const step = CHAT_STEPS[id];
  const human = id === HUMAN;
  return (
    <button
      type="button"
      aria-pressed={active}
      aria-controls={PANEL_ID}
      data-step={id}
      data-layout="wide"
      aria-label={`${index + 1}. ${step.title}`}
      onClick={() => onPick(id)}
      onMouseEnter={() => onPeek(id)}
      onFocus={() => onPick(id)}
      className={`absolute flex flex-col justify-center rounded-row border-2 py-2 pr-16 pl-4 text-left transition-[border-color,box-shadow,background-color] ${
        active
          ? "border-accent bg-surface shadow-[0_10px_30px_-16px_rgb(40_30_120/0.45)]"
          : human
            ? "border-accent/50 bg-surface hover:border-accent"
            : "border-line bg-surface hover:border-ink-2/40"
      }`}
      style={{ left: BOX.x, top: chatBoxTop(id), width: BOX.width, height: BOX.height }}
    >
      <span className="flex items-baseline gap-2">
        <span className="font-mono text-xs text-ink-2">{index + 1}</span>
        <span className={`font-extrabold ${human ? "text-accent" : ""}`}>{step.title}</span>
      </span>
      <span className="mt-0.5 text-[13px] leading-snug text-ink-2">{step.short}</span>
    </button>
  );
}

function Panel({ step: id }: { step: ChatStepId }) {
  const step = CHAT_STEPS[id];
  const index = CHAT_ORDER.indexOf(id);
  return (
    <div
      id={PANEL_ID}
      aria-live="polite"
      className="rounded-card border border-line bg-surface p-7"
    >
      <div className="flex items-end gap-4">
        <Bleep mood={step.mood} size={88} />
        <p className="pb-2 font-mono text-sm text-ink-2">
          Step {index + 1} of {CHAT_ORDER.length}
        </p>
      </div>
      <h2
        className={`mt-5 text-2xl font-extrabold tracking-tight ${id === HUMAN ? "text-accent" : ""}`}
      >
        {step.title}
      </h2>
      <p className="mt-3 text-[17px] leading-relaxed text-ink-2">{step.text}</p>
      <Ways next={thenOf(id)} ways={chatBranches(id)} />
      <a
        href={blob(step.code)}
        className="mt-6 inline-block font-mono text-[13px] text-accent underline-offset-4 hover:underline"
      >
        {step.code.replace("backend/src/bleep/", "")}
      </a>
    </div>
  );
}

function Narrow({ active, onIncident }: { active: ChatStepId; onIncident: () => void }) {
  return (
    <>
      <p className="mb-3 font-bold text-ink-2">Where you write</p>
      <ul className="mb-4 flex flex-wrap gap-2">
        {CHAT_SOURCES.map((source) => (
          <li
            key={source}
            className="rounded-full border border-line bg-surface px-3 py-1.5 text-sm font-semibold text-ink-2"
          >
            {source}
          </li>
        ))}
      </ul>
      <ol className="flex flex-col">
        {CHAT_ORDER.map((id, index) => (
          <NarrowStep
            key={id}
            id={id}
            index={index}
            active={id === active}
            onIncident={onIncident}
          />
        ))}
      </ol>
    </>
  );
}

function NarrowStep({
  id,
  index,
  active,
  onIncident,
}: {
  id: ChatStepId;
  index: number;
  active: boolean;
  onIncident: () => void;
}) {
  const step = CHAT_STEPS[id];
  const human = id === HUMAN;
  return (
    <li className="flex flex-col">
      {index > 0 && <span aria-hidden className="mx-auto h-6 w-0.5 rounded-full bg-line" />}
      <div
        data-step={id}
        data-layout="narrow"
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
        <Ways next={thenOf(id)} ways={chatBranches(id)} />
        {/* On a wide screen this list is only read aloud, and the diagram has the button. */}
        {leadsIntoIncident(id) && (
          <button
            type="button"
            onClick={onIncident}
            aria-label={`${CHAT_ENDS.incident.title}: show the incident diagram`}
            className="mt-4 inline-flex min-h-11 items-center rounded-full border-2 border-accent/50 px-4 text-sm font-bold text-accent hover:border-accent xl:hidden"
          >
            {CHAT_ENDS.incident.title} <span aria-hidden>&nbsp;→</span>
          </button>
        )}
        {active && <Bleep mood={step.mood} size={44} className="absolute top-4 right-4" />}
      </div>
    </li>
  );
}
