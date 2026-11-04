import { describe, expect, it } from "vitest";

import { event } from "@/components/incident/fixtures";
import { progressSteps } from "@/components/incident/progress";
import type { IncidentEvent, IncidentStatus } from "@/lib/api-types";

/** `node_started` and `node_finished` for each node, in order. */
function ran(...nodes: string[]): IncidentEvent[] {
  return nodes.flatMap((node) => [
    event("node_started", { node }),
    event("node_finished", { node }),
  ]);
}

const started = (node: string) => event("node_started", { node });

function states(events: IncidentEvent[], status: IncidentStatus) {
  return Object.fromEntries(
    progressSteps(events, status).map(({ label, state }) => [label, state]),
  );
}

describe("progressSteps", () => {
  it("marks the finished steps done and the running one current", () => {
    expect(states([...ran("gather"), started("investigate")], "investigating")).toEqual({
      Gather: "done",
      Investigate: "current",
      Diagnose: "todo",
      Plan: "todo",
      "Your yes": "todo",
      Fix: "todo",
      Check: "todo",
      Report: "todo",
    });
  });

  it("waits on Your yes while the plan waits", () => {
    const events = [...ran("gather", "investigate", "diagnose", "propose"), started("approval")];
    const steps = states(events, "awaiting_approval");
    expect(steps["Plan"]).toBe("done");
    expect(steps["Your yes"]).toBe("current");
  });

  it("shows Your yes as current from the status while the history is still loading", () => {
    expect(states([], "awaiting_approval")["Your yes"]).toBe("current");
  });

  it("leaves Fix and Check grey when nothing ran, and the report done", () => {
    const events = ran("gather", "investigate", "diagnose", "propose", "approval", "report");
    const steps = states(events, "closed");
    expect(steps["Fix"]).toBe("todo");
    expect(steps["Check"]).toBe("todo");
    expect(steps["Report"]).toBe("done");
    expect(Object.values(steps)).not.toContain("current");
  });

  it("shows a closed incident's report done even without its node events", () => {
    expect(states([], "closed")["Report"]).toBe("done");
  });

  it("marks the step a failed run stopped at", () => {
    const events = [...ran("gather"), started("investigate")];
    const steps = states(events, "failed");
    expect(steps["Gather"]).toBe("done");
    expect(steps["Investigate"]).toBe("stopped");
    expect(Object.values(steps)).not.toContain("current");
  });

  it("clears the later steps when a failed check sends the run back to investigate", () => {
    const first = ["gather", "investigate", "diagnose", "propose", "approval", "execute", "verify"];
    const steps = states([...ran(...first), started("investigate")], "investigating");
    expect(steps["Gather"]).toBe("done");
    expect(steps["Investigate"]).toBe("current");
    for (const label of ["Diagnose", "Plan", "Your yes", "Fix", "Check"]) {
      expect(steps[label], label).toBe("todo");
    }
  });

  const action = (status: string) =>
    event("action", { action: "docker.start", target: "shop-web-1", status });
  const verification = (ok: boolean) => event("verification", { ok, attempt: 1, checks: [] });
  const decision = (kind: string) => event("decision", { kind, text: null, via: "dashboard" });
  const upToApproval = () => ran("gather", "investigate", "diagnose", "propose");
  const answered = (kind: string) => [
    ...upToApproval(),
    started("approval"),
    decision(kind),
    ...ran("approval"),
  ];
  const step = (nodeEvents: IncidentEvent[], ...between: IncidentEvent[]) => [
    nodeEvents[0],
    ...between,
    nodeEvents[1],
  ];

  // How each way a run ends shows: only a run whose checks passed is all green.
  it.each([
    {
      name: "resolved",
      events: () => [
        ...answered("approve"),
        ...step(ran("execute"), action("started"), action("done")),
        ...step(ran("verify"), verification(true)),
        ...ran("report"),
      ],
      status: "resolved" as const,
      expected: ["done", "done", "done", "done", "done", "done", "done", "done"],
    },
    {
      name: "unverified: the checks never passed",
      events: () => [
        ...answered("approve"),
        ...step(ran("execute"), action("done")),
        ...step(ran("verify"), verification(false), verification(false)),
        ...ran("report"),
      ],
      status: "closed" as const,
      expected: ["done", "done", "done", "done", "done", "done", "failed", "done"],
    },
    {
      name: "budget: stopped after a check that failed",
      events: () => [
        ...answered("approve"),
        ...step(ran("execute"), action("done")),
        ...step(ran("verify"), verification(false)),
        ...ran("report"),
      ],
      status: "closed" as const,
      expected: ["done", "done", "done", "done", "done", "done", "failed", "done"],
    },
    {
      name: "a step of the plan failed",
      events: () => [
        ...answered("approve"),
        ...step(ran("execute"), action("started"), action("failed")),
        ...ran("report"),
      ],
      status: "closed" as const,
      expected: ["done", "done", "done", "done", "done", "failed", "todo", "done"],
    },
    {
      name: "the run failed in the middle of a step",
      events: () => [...answered("approve"), started("execute"), action("started")],
      status: "failed" as const,
      expected: ["done", "done", "done", "done", "done", "stopped", "todo", "todo"],
    },
    {
      name: "rejected",
      events: () => [...answered("reject"), ...ran("report")],
      status: "closed" as const,
      expected: ["done", "done", "done", "done", "declined", "todo", "todo", "done"],
    },
    {
      name: "no action",
      events: () => [...upToApproval(), ...ran("report")],
      status: "closed" as const,
      expected: ["done", "done", "done", "done", "todo", "todo", "todo", "done"],
    },
  ])("shows a run that ended as $name", ({ events, status, expected }) => {
    expect(progressSteps(events(), status).map(({ state }) => state)).toEqual(expected);
  });

  it("marks no step as stopped for a failed run whose history has not loaded", () => {
    expect(Object.values(states([], "failed"))).not.toContain("stopped");
  });

  it("forgets a failed check once the run checks again and passes", () => {
    const events = [
      ...answered("approve"),
      ...step(ran("execute"), action("done")),
      ...step(ran("verify"), verification(false)),
      ...ran("investigate", "diagnose", "propose"),
      started("approval"),
      decision("approve"),
      ...ran("approval"),
      ...step(ran("execute"), action("done")),
      ...step(ran("verify"), verification(true)),
    ];
    const steps = states(events, "resolved");
    expect(steps["Check"]).toBe("done");
  });
});
