import { EDGES, GRAPH_NODES, SOURCES, STEP_ORDER, edgeId, type Edge, type StepId } from "./flow";

/** The wide diagram's frame, in px: the steps stand in one column, the loops back run beside it
 * and an early end leaves to the right as a short arrow to a "Report" tag. */
export const WIDTH = 700;
export const BOX = { x: 140, width: 280, height: 72 } as const;
const BOX_RIGHT = BOX.x + BOX.width;
const CENTER = BOX.x + BOX.width / 2;
const PITCH = 112;
const TOP = 104;
/** Extra room above the first graph step for the graph's own label. */
const GRAPH_GAP = 36;
const LEFT_LANE = BOX.x - 28;
const RIGHT_LANE = BOX_RIGHT + 188;
const END_STUB = 100;
const SHORT_LOOP_X = CENTER + 64;
const CORNER = 10;

/** The side each loop back that skips more than one step runs on, chosen so no lines cross. */
const LONG_LOOP_SIDE: Record<string, "left" | "right"> = {
  "execute-propose": "left",
  "verify-investigate": "right",
};

const row = (step: StepId) => STEP_ORDER.indexOf(step);

export function boxTop(step: StepId): number {
  const index = row(step);
  const inGraph = index >= STEP_ORDER.indexOf(GRAPH_NODES[0]);
  return TOP + index * PITCH + (inGraph ? GRAPH_GAP : 0);
}

const middle = (step: StepId) => boxTop(step) + BOX.height / 2;
const bottom = (step: StepId) => boxTop(step) + BOX.height;

export const HEIGHT = bottom(STEP_ORDER.at(-1)!) + 24;

/** The tinted area behind the graph's steps, and where its label sits. */
export const GRAPH_AREA = {
  x: 0,
  y: boxTop(GRAPH_NODES[0]) - 48,
  width: WIDTH,
  height: bottom(GRAPH_NODES.at(-1)!) + 20 - (boxTop(GRAPH_NODES[0]) - 48),
  labelX: 20,
  labelY: boxTop(GRAPH_NODES[0]) - 22,
};

export type Direction = "up" | "down" | "left" | "right";

export interface Label {
  x: number;
  y: number;
  anchor: "start" | "end";
  text: string;
}

export interface DrawnEdge {
  id: string;
  edge: Edge;
  path: string;
  head: { x: number; y: number; to: Direction };
  label?: Label;
  /** The "Report" tag an early end points at. */
  tag?: { x: number; y: number; width: number; height: number };
}

/** An orthogonal path through `points`, with each bend rounded. */
export function rounded(points: [number, number][]): string {
  const [first, ...rest] = points;
  let path = `M${first![0]} ${first![1]}`;
  for (let index = 0; index < rest.length; index++) {
    const [x, y] = rest[index]!;
    const next = rest[index + 1];
    if (!next) {
      path += ` L${x} ${y}`;
      break;
    }
    const [px, py] = index === 0 ? first! : rest[index - 1]!;
    const into = Math.min(CORNER, Math.hypot(x - px, y - py) / 2);
    const out = Math.min(CORNER, Math.hypot(next[0] - x, next[1] - y) / 2);
    const before: [number, number] = [x - Math.sign(x - px) * into, y - Math.sign(y - py) * into];
    const after: [number, number] = [
      x + Math.sign(next[0] - x) * out,
      y + Math.sign(next[1] - y) * out,
    ];
    path += ` L${before[0]} ${before[1]} Q${x} ${y} ${after[0]} ${after[1]}`;
  }
  return path;
}

function draw(edge: Edge): DrawnEdge {
  const id = edgeId(edge);
  const span = row(edge.from) - row(edge.to);
  if (edge.kind === "next") {
    const from = bottom(edge.from);
    const to = boxTop(edge.to);
    return {
      id,
      edge,
      path: rounded([
        [CENTER, from],
        [CENTER, to],
      ]),
      head: { x: CENTER, y: to, to: "down" },
      label: edge.when
        ? { x: CENTER - 12, y: (from + to) / 2 + 4, anchor: "end", text: edge.when }
        : undefined,
    };
  }
  if (edge.kind === "end") {
    const y = middle(edge.from) + 10;
    const end = BOX_RIGHT + END_STUB;
    return {
      id,
      edge,
      path: rounded([
        [BOX_RIGHT, y],
        [end, y],
      ]),
      head: { x: end, y, to: "right" },
      label: { x: BOX_RIGHT + 12, y: y - 7, anchor: "start", text: edge.when ?? "" },
      tag: { x: end + 2, y: y - 12, width: 66, height: 24 },
    };
  }
  if (span === 1) {
    const from = boxTop(edge.from);
    const to = bottom(edge.to);
    return {
      id,
      edge,
      path: rounded([
        [SHORT_LOOP_X, from],
        [SHORT_LOOP_X, to],
      ]),
      head: { x: SHORT_LOOP_X, y: to, to: "up" },
      label: { x: SHORT_LOOP_X + 12, y: (from + to) / 2 + 4, anchor: "start", text: edge.when! },
    };
  }
  const side = LONG_LOOP_SIDE[id];
  if (!side) throw new Error(`no side for the loop back ${id}`);
  const lane = side === "left" ? LEFT_LANE : RIGHT_LANE;
  const edgeX = side === "left" ? BOX.x : BOX_RIGHT;
  const leave = middle(edge.from) + (side === "left" ? 12 : 0);
  const enter = middle(edge.to) - 16;
  return {
    id,
    edge,
    path: rounded([
      [edgeX, leave],
      [lane, leave],
      [lane, enter],
      [edgeX, enter],
    ]),
    head: { x: edgeX, y: enter, to: side === "left" ? "right" : "left" },
    label: {
      x: side === "left" ? lane - 10 : lane + 10,
      y: (leave + enter) / 2 + 4,
      anchor: side === "left" ? "end" : "start",
      text: edge.when!,
    },
  };
}

export const DRAWN_EDGES: readonly DrawnEdge[] = EDGES.map(draw);

/** The three things the watcher reads, as tags across the top, each with a line into Notices. */
export const SOURCE_TAGS = SOURCES.map((text, index) => {
  const gap = 14;
  const width = (WIDTH - gap * (SOURCES.length - 1)) / SOURCES.length;
  const x = index * (width + gap);
  const from: [number, number] = [x + width / 2, 38];
  const to: [number, number] = [CENTER, TOP];
  const bend = (from[1] + to[1]) / 2;
  return {
    text,
    x,
    y: 8,
    width,
    height: 30,
    path: `M${from[0]} ${from[1]} C${from[0]} ${bend} ${to[0]} ${bend} ${to[0]} ${to[1]}`,
  };
});

/** A small filled triangle whose tip is at (x, y), pointing `to`. */
export function arrowHead({ x, y, to }: DrawnEdge["head"]): string {
  const size = 7;
  const half = 4.5;
  const points: Record<Direction, [number, number][]> = {
    down: [
      [x, y],
      [x - half, y - size],
      [x + half, y - size],
    ],
    up: [
      [x, y],
      [x - half, y + size],
      [x + half, y + size],
    ],
    right: [
      [x, y],
      [x - size, y - half],
      [x - size, y + half],
    ],
    left: [
      [x, y],
      [x + size, y - half],
      [x + size, y + half],
    ],
  };
  return points[to].map(([px, py]) => `${px},${py}`).join(" ");
}
