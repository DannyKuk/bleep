import {
  CHAT_EDGES,
  CHAT_ORDER,
  CHAT_SOURCES,
  chatEdgeId,
  type ChatEdge,
  type ChatStepId,
} from "./chat";
import { BOX, WIDTH, rounded, type Direction, type Label } from "./geometry";

/** The chat's frame shares the incident diagram's column: the steps stand where its steps stand,
 * and each way out leaves to the right as a short arrow, its label above, to a tag. */
const BOX_RIGHT = BOX.x + BOX.width;
const CENTER = BOX.x + BOX.width / 2;
const PITCH = 112;
const TOP = 104;
const TAG_X = BOX_RIGHT + 60;
const FAN_X = BOX_RIGHT + 20;
/** Two ways out of one step leave its box this far apart and reach tags this far apart. */
const LEAVE_GAP = 24;
const TAG_GAP = 54;
const TAG_HEIGHT = 26;
/** The tag that leads into the incident loop is a button with a second line. */
const LINK_HEIGHT = 52;

const row = (step: ChatStepId) => CHAT_ORDER.indexOf(step);

export function chatBoxTop(step: ChatStepId): number {
  return TOP + row(step) * PITCH;
}

const middle = (step: ChatStepId) => chatBoxTop(step) + BOX.height / 2;
const bottom = (step: ChatStepId) => chatBoxTop(step) + BOX.height;

export const CHAT_HEIGHT = bottom(CHAT_ORDER.at(-1)!) + 24;

export interface DrawnChatEdge {
  id: string;
  edge: ChatEdge;
  path: string;
  head: { x: number; y: number; to: Direction };
  label?: Label;
  tag?: { x: number; y: number; width: number; height: number };
}

function draw(edge: ChatEdge): DrawnChatEdge {
  const id = chatEdgeId(edge);
  if (edge.kind === "next") {
    const from = bottom(edge.from);
    const to = chatBoxTop(edge.to);
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
  const siblings = CHAT_EDGES.filter((other) => other.from === edge.from && other.kind === "end");
  const offset = siblings.indexOf(edge) - (siblings.length - 1) / 2;
  const leave = middle(edge.from) + offset * LEAVE_GAP;
  const reach = middle(edge.from) + offset * TAG_GAP;
  const points: [number, number][] =
    leave === reach
      ? [
          [BOX_RIGHT, leave],
          [TAG_X, reach],
        ]
      : [
          [BOX_RIGHT, leave],
          [FAN_X, leave],
          [FAN_X, reach],
          [TAG_X, reach],
        ];
  const height = edge.to === "incident" ? LINK_HEIGHT : TAG_HEIGHT;
  return {
    id,
    edge,
    path: rounded(points),
    head: { x: TAG_X, y: reach, to: "right" },
    label: {
      x: (leave === reach ? BOX_RIGHT : FAN_X) + 12,
      y: reach - height / 2 - 6,
      anchor: "start",
      text: edge.when,
    },
    tag: { x: TAG_X + 2, y: reach - height / 2, width: WIDTH - TAG_X - 2, height },
  };
}

export const DRAWN_CHAT_EDGES: readonly DrawnChatEdge[] = CHAT_EDGES.map(draw);

/** The two places a message comes from, as tags across the top, each with a line into "You
 * write". */
export const CHAT_SOURCE_TAGS = CHAT_SOURCES.map((text, index) => {
  const gap = 14;
  const width = (WIDTH - gap * (CHAT_SOURCES.length - 1)) / CHAT_SOURCES.length;
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
