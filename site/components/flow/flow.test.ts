import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { EDGES, GRAPH_NODES, OPENING_NODES, STEP_ORDER, STEPS, branches, edgeId } from "./flow";

const build = readFileSync(
  path.join(import.meta.dirname, "../../../backend/src/bleep/graph/build.py"),
  "utf8",
);

/** The node names in `for module in (gather, investigate, ...)`, in order. */
function graphNodes(source: string): string[] {
  const tuple = /for module in \(([^)]*)\)/.exec(source);
  if (!tuple) throw new Error("no `for module in (...)` in build.py");
  return tuple[1]!.split(",").map((name) => name.trim());
}

/** Every `add_edge(a, b)` and `add_conditional_edges(a, route, [b, c])`, as `a-b`. */
function graphEdges(source: string): string[] {
  const plain = [...source.matchAll(/add_edge\(\s*(\w+|"\w+")\s*,\s*(\w+|"\w+")\s*\)/g)].map(
    ([, from, to]) => `${unquote(from!)}-${unquote(to!)}`,
  );
  const routed = [
    ...source.matchAll(/add_conditional_edges\(\s*"(\w+)"\s*,\s*[\w.]+\s*,\s*\[([^\]]*)\]\s*\)/g),
  ].flatMap(([, from, targets]) =>
    targets!.split(",").map((target) => `${from}-${unquote(target.trim())}`),
  );
  return [...plain, ...routed];
}

const unquote = (name: string) => name.replace(/^"|"$/g, "");

describe("the flow page's graph", () => {
  const nodes = graphNodes(build);
  const edges = graphEdges(build);

  it("reads build.py at all", () => {
    expect(nodes).toHaveLength(8);
    expect(edges).toHaveLength(17);
  });

  it("has the graph's nodes, in its order", () => {
    expect([...GRAPH_NODES]).toEqual(nodes);
  });

  it("has every edge of the graph and no other", () => {
    const fromGraph = edges.filter((edge) => !edge.startsWith("START-") && !edge.endsWith("-END"));
    const drawn = EDGES.filter(
      (edge) => !(OPENING_NODES as readonly string[]).includes(edge.from),
    ).map(edgeId);
    expect([...drawn].sort()).toEqual([...fromGraph].sort());
  });

  it("enters the graph where build.py starts it and ends where it ends", () => {
    expect(edges).toContain(`START-${GRAPH_NODES[0]}`);
    expect(edges).toContain(`${GRAPH_NODES.at(-1)}-END`);
    expect(EDGES.map(edgeId)).toContain(`${OPENING_NODES.at(-1)}-${GRAPH_NODES[0]}`);
  });
});

describe("the flow page's steps", () => {
  it("each have their own words and a file that exists", () => {
    for (const id of STEP_ORDER) {
      const step = STEPS[id];
      expect(step.short.length, id).toBeLessThanOrEqual(52);
      expect(step.text.length, id).toBeGreaterThan(step.short.length);
      expect(
        () => readFileSync(path.join(import.meta.dirname, "../../..", step.code)),
        id,
      ).not.toThrow();
    }
  });

  it("say where a step may lead other than straight down", () => {
    expect(branches("approval")).toEqual([
      "You tell it something: Proposes",
      "Yes: Runs the fix",
      "No: Reports",
    ]);
    expect(branches("gather")).toEqual([]);
  });

  it("label every edge that is not the way straight down", () => {
    for (const edge of EDGES.filter((edge) => edge.kind !== "next")) {
      expect(edge.when, edgeId(edge)).toBeTruthy();
    }
  });
});
