import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  ACTION_KINDS,
  CHAT_EDGES,
  CHAT_ORDER,
  CHAT_STEPS,
  TALK_KINDS,
  chatBranches,
  chatEdgeId,
  type ChatStepId,
  type Covered,
} from "./chat";

const repo = path.join(import.meta.dirname, "../../..");
const read = (file: string) => readFileSync(path.join(repo, "backend/src/bleep", file), "utf8");

/** The values of `<name> = Literal[...]` at the start of a line, over several lines too. */
function literal(source: string, name: string): string[] {
  const found = new RegExp(`^${name} = Literal\\[([^\\]]*)\\]`, "m").exec(source);
  if (!found) throw new Error(`no ${name} = Literal[...] in schemas/talk.py`);
  return [...found[1]!.matchAll(/"(\w+)"/g)].map(([, value]) => value!);
}

/** The number in `<name> = <call>(<unit>=N)` or `<name> = N`. */
function constant(source: string, name: string): number {
  const found = new RegExp(`^${name} = (?:\\w+\\(\\w+=)?(\\d+)`, "m").exec(source);
  if (!found) throw new Error(`no ${name} in the source`);
  return Number(found[1]);
}

/** The words a step or an edge shows: a step's text, an edge's label. */
function shown(at: string): string {
  if ((CHAT_ORDER as readonly string[]).includes(at)) return CHAT_STEPS[at as ChatStepId].text;
  const edge = CHAT_EDGES.find((candidate) => chatEdgeId(candidate) === at);
  if (!edge) throw new Error(`${at} is neither a step nor an edge of the chat`);
  return edge.when ?? "";
}

function expectCovered(covered: Record<string, Covered>) {
  for (const [kind, { at, says }] of Object.entries(covered)) {
    expect(shown(at).toLowerCase(), kind).toContain(says.toLowerCase());
  }
}

describe("the flow page's chat", () => {
  const talk = read("schemas/talk.py");

  it("covers every kind the model reads a message as, and no other", () => {
    const kinds = literal(talk, "TalkKind");
    expect(kinds.length).toBeGreaterThan(0);
    expect(Object.keys(TALK_KINDS).sort()).toEqual([...kinds].sort());
    expectCovered(TALK_KINDS);
  });

  it("covers every action a chat can offer, and no other", () => {
    const actions = literal(talk, "ActionKind");
    expect(actions.length).toBeGreaterThan(0);
    expect(Object.keys(ACTION_KINDS).sort()).toEqual([...actions].sort());
    expectCovered(ACTION_KINDS);
  });

  it("says the numbers the code uses", () => {
    const turns = constant(read("talk/history.py"), "TURNS_KEPT");
    const minutes = constant(read("talk/confirms.py"), "CONFIRM_TTL");
    const hours = constant(read("talk/state.py"), "RECENT");
    const days = constant(read("incidents/quiet.py"), "MAX_SNOOZE");
    expect(CHAT_STEPS.write.text).toContain(`last ${turns} turns`);
    expect(CHAT_STEPS.offer.short).toContain(`${minutes} minutes`);
    expect(CHAT_STEPS.offer.text).toContain(`${minutes} minutes`);
    expect(CHAT_STEPS.read.text).toContain(`${hours} hours`);
    expect(CHAT_STEPS.check.text).toContain(`${days} days`);
  });
});

describe("the chat's steps", () => {
  it("each have their own words and a file that exists", () => {
    for (const id of CHAT_ORDER) {
      const step = CHAT_STEPS[id];
      expect(step.short.length, id).toBeLessThanOrEqual(52);
      expect(step.text.length, id).toBeGreaterThan(step.short.length);
      expect(() => readFileSync(path.join(repo, step.code)), id).not.toThrow();
    }
  });

  it("go one way down, each step to the next", () => {
    const down = CHAT_EDGES.filter((edge) => edge.kind === "next").map(chatEdgeId);
    const spine = CHAT_ORDER.slice(1).map((to, index) => `${CHAT_ORDER[index]}-${to}`);
    expect(down).toEqual(spine);
  });

  it("label every way out, at most two a step", () => {
    for (const id of CHAT_ORDER) {
      const out = CHAT_EDGES.filter((edge) => edge.from === id && edge.kind === "end");
      expect(out.length, id).toBeLessThanOrEqual(2);
      for (const edge of out) expect(edge.when, chatEdgeId(edge)).toBeTruthy();
    }
  });

  it("say where a step may lead other than straight down", () => {
    expect(chatBranches("read")).toEqual([
      "An action: Code checks it",
      "A question: Answers from its own state",
      "Can't read it, or won't do it: Says so in one line",
    ]);
    expect(chatBranches("write")).toEqual([]);
  });
});
