import { describe, expect, it } from "vitest";

import { event, proposal } from "@/components/incident/fixtures";
import { planRun, stepStatuses } from "@/components/incident/plan-run";
import type { PlanStep } from "@/lib/api-types";

const step = (action: PlanStep["action"], target: string): PlanStep => ({
  action,
  host_id: "demo",
  target,
  params: {},
});

const action = (name: string, target: string, status: string) =>
  event("action", { action: name, target, status });

describe("planRun", () => {
  it("keeps each step's latest status and the latest check", () => {
    const run = planRun([
      event("proposal", {}),
      action("docker.start", "shop-db-1", "started"),
      action("docker.start", "shop-db-1", "done"),
      action("docker.restart", "shop-api-1", "started"),
      event("verification", { ok: false, attempt: 1, checks: [] }),
      event("verification", {
        ok: true,
        attempt: 2,
        checks: [{ kind: "container", target: "shop-db-1", ok: true, detail: "running" }],
      }),
    ]);
    expect(run.steps).toEqual([
      { action: "docker.start", target: "shop-db-1", status: "done" },
      { action: "docker.restart", target: "shop-api-1", status: "started" },
    ]);
    expect(run.verification).toEqual({
      ok: true,
      attempt: 2,
      checks: [{ kind: "container", target: "shop-db-1", ok: true, detail: "running" }],
    });
  });

  it("forgets an earlier plan's steps and checks once a new plan is proposed", () => {
    const run = planRun([
      event("proposal", {}),
      action("docker.restart", "shop-web-1", "done"),
      event("verification", { ok: false, attempt: 1, checks: [] }),
      event("proposal", {}),
    ]);
    expect(run).toEqual({ steps: [], verification: null });
  });
});

describe("stepStatuses", () => {
  it("matches statuses to the plan's steps in order, the same step twice included", () => {
    const steps = [
      step("docker.restart", "shop-web-1"),
      step("docker.start", "shop-db-1"),
      step("docker.restart", "shop-web-1"),
    ];
    const run = planRun([
      action("docker.restart", "shop-web-1", "started"),
      action("docker.restart", "shop-web-1", "done"),
      action("docker.start", "shop-db-1", "started"),
      action("docker.start", "shop-db-1", "failed"),
      action("docker.restart", "shop-web-1", "skipped"),
    ]);
    expect(stepStatuses(steps, run)).toEqual(["done", "failed", "skipped"]);
  });

  it("leaves a step that has not started without a status", () => {
    const run = planRun([action("docker.start", "shop-web-1", "started")]);
    expect(stepStatuses([...proposal().steps, step("docker.stop", "x")], run)).toEqual([
      "started",
      undefined,
    ]);
  });
});
