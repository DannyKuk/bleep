import type { Step } from "./flow";

/** The chat's way from a message to what it changes, top to bottom, as the page draws it. */
export const CHAT_ORDER = ["write", "read", "check", "offer", "run"] as const;

export type ChatStepId = (typeof CHAT_ORDER)[number];

export const CHAT_STEPS: Record<ChatStepId, Step> = {
  write: {
    title: "You write",
    mood: "watching",
    short: "In Telegram or the dashboard's chat panel",
    text: "You write to Bleep in Telegram or in the dashboard's chat panel, about anything, or about one incident as a reply or from its page. Your text is capped, and the last 4 turns of the chat come along, so a follow-up reads in context. Each surface keeps its own turns, in memory only.",
    code: "backend/src/bleep/talk/history.py",
  },
  read: {
    title: "Bleep reads it",
    mood: "working",
    short: "Its own state, then one cheap-model call",
    text: "Code builds a snapshot of Bleep's own state, with no model: hosts, services, open incidents and those that ended in the last 24 hours. One call to the cheap model then reads what kind of message it is, with your text, the turns and the snapshot all marked as untrusted. A question is answered from that state, with links only to URLs Bleep watches. A message it could not read or understand, or one asking for a change or a secret, gets one fixed line. Written about an incident whose plan waits, all but the secret become an offer to send your words to that plan.",
    code: "backend/src/bleep/talk/conversation.py",
  },
  check: {
    title: "Code checks it",
    mood: "working",
    short: "Against Bleep's state, never the model's word",
    text: "Code checks the action the model read against Bleep's state: the service or host exists, a snooze ends within 7 days, the incident is still open, and for words to a plan, a plan waits. The offer is tied to that plan. An action that does not fit is answered with why, and nothing is offered.",
    code: "backend/src/bleep/talk/offers.py",
  },
  offer: {
    title: "Offers it",
    mood: "needs_you",
    short: "Yes or No, good for 10 minutes",
    text: "Bleep says what it would do in a fixed line built from what code checked, under two buttons: Yes, or Send for words to a plan, and No. Nothing has changed yet. Only a press of Yes runs it, once, within 10 minutes; No, a second press or a late one changes nothing. A “yes, do it” to a waiting plan offers nothing new: Telegram shows the plan again with its own buttons, and the dashboard points to its page.",
    code: "backend/src/bleep/talk/confirms.py",
  },
  run: {
    title: "Does it",
    mood: "all_good",
    short: "Only what you said yes to",
    text: "A press of Yes runs the offer through the same code as the dashboard's own buttons. A snooze, leave alone or watch again sets or clears the quiet, and “it's expected” records it and closes the incident. A look opens an incident, and your words for a plan make Bleep plan again; both run the incident loop, where any fix still waits for your yes. If the incident ended meanwhile, nothing changes.",
    code: "backend/src/bleep/talk/actions.py",
  },
};

/** Where a way out of the chat leads: a line Bleep answers with, or the incident loop. */
export const CHAT_ENDS = {
  answer: { title: "Answers from its own state" },
  line: { title: "Says so in one line" },
  why: { title: "Says why" },
  nothing: { title: "Nothing changes" },
  incident: { title: "The incident loop", note: "any fix still waits for your yes" },
} as const;

export type ChatEndId = keyof typeof CHAT_ENDS;

/** The end that leads into the incident diagram; the page links it to "An incident". */
export const INTO_INCIDENT: ChatEndId = "incident";

export type ChatEdge =
  | { from: ChatStepId; to: ChatStepId; kind: "next"; when?: string }
  | { from: ChatStepId; to: ChatEndId; kind: "end"; when: string };

/** One way down, and a labelled way out to the side where the chat stops or leaves. */
export const CHAT_EDGES: readonly ChatEdge[] = [
  { from: "write", to: "read", kind: "next" },
  { from: "read", to: "check", kind: "next", when: "an action" },
  { from: "read", to: "answer", kind: "end", when: "a question" },
  { from: "read", to: "line", kind: "end", when: "can't read it, or won't do it" },
  { from: "check", to: "offer", kind: "next" },
  { from: "check", to: "why", kind: "end", when: "does not fit" },
  { from: "offer", to: "run", kind: "next", when: "yes" },
  { from: "offer", to: "nothing", kind: "end", when: "no, or too late" },
  { from: "run", to: "incident", kind: "end", when: "a look, or your words for a plan" },
];

/** Where the chat comes from, feeding "You write". */
export const CHAT_SOURCES: readonly string[] = ["Telegram", "The dashboard's chat panel"];

export const chatEdgeId = (edge: Pick<ChatEdge, "from" | "to">) => `${edge.from}-${edge.to}`;

export const chatEdgesFrom = (step: ChatStepId) => CHAT_EDGES.filter((edge) => edge.from === step);

const titleOf = (edge: ChatEdge) =>
  edge.kind === "next" ? CHAT_STEPS[edge.to].title : CHAT_ENDS[edge.to].title;

/** The step's ways out other than straight down, in words, for the phone and the screen reader. */
export function chatBranches(step: ChatStepId): string[] {
  return chatEdgesFrom(step)
    .filter((edge) => edge.kind !== "next" || edge.when)
    .map((edge) => `${edge.when ? `${capitalise(edge.when)}: ` : ""}${titleOf(edge)}`);
}

const capitalise = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

/** The kinds the model reads a message as (`TalkKind` in `backend/src/bleep/schemas/talk.py`). */
export type TalkKind = "question" | "action" | "refusal" | "unclear";

/** The actions a chat can offer (`ActionKind` in the same file). */
export type ActionKind =
  "snooze" | "leave_alone" | "watch_again" | "expected" | "instruct" | "show_plan" | "look_again";

/** Where the page shows each kind and action: a step, or an edge by its id, and the words
 * there that name it. A test reads the backend's kinds and fails on one this does not cover. */
export interface Covered {
  at: ChatStepId | string;
  says: string;
}

export const TALK_KINDS: Record<TalkKind, Covered> = {
  question: { at: "read-answer", says: "a question" },
  action: { at: "read-check", says: "an action" },
  refusal: { at: "read-line", says: "won't do it" },
  unclear: { at: "read-line", says: "can't read it" },
};

export const ACTION_KINDS: Record<ActionKind, Covered> = {
  snooze: { at: "run", says: "snooze" },
  leave_alone: { at: "run", says: "leave alone" },
  watch_again: { at: "run", says: "watch again" },
  expected: { at: "run", says: "it's expected" },
  instruct: { at: "run-incident", says: "your words for a plan" },
  show_plan: { at: "offer", says: "yes, do it" },
  look_again: { at: "run-incident", says: "a look" },
};

/** How long the walk stays on each step, in ms; it lingers where you are asked and at the end. */
export const CHAT_DWELL: Record<ChatStepId, number> = {
  write: 2000,
  read: 2400,
  check: 1800,
  offer: 3200,
  run: 3200,
};
