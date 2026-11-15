import { describe, expect, it } from "vitest";

import { dwell, servicesAt, START, step, TOOL_CALLS, type ReplayState } from "./script";

function tickUntilStill(state: ReplayState): ReplayState {
  let current = state;
  for (let i = 0; i < 50 && dwell(current) !== null; i += 1) current = step(current, "tick");
  return current;
}

describe("the replay", () => {
  it("shows every tool call, then stops and waits for the visitor", () => {
    const waiting = tickUntilStill(START);
    expect(waiting).toEqual({ phase: "asks", shown: TOOL_CALLS.length });
    expect(step(waiting, "tick")).toEqual(waiting);
  });

  it("runs the fix only after a yes, then verifies before it says fixed", () => {
    const running = step(tickUntilStill(START), "approve");
    expect(running.phase).toBe("running");
    expect(step(running, "tick").phase).toBe("checking");
    expect(tickUntilStill(running).phase).toBe("fixed");
  });

  it("changes nothing after a no", () => {
    const declined = step(tickUntilStill(START), "decline");
    expect(declined.phase).toBe("declined");
    expect(dwell(declined)).toBeNull();
    expect(servicesAt(declined.phase)).toEqual(servicesAt("asks"));
  });

  it("ignores a yes or a no before Bleep asks", () => {
    const investigating = step(step(START, "tick"), "tick");
    expect(step(investigating, "approve")).toEqual(investigating);
    expect(step(investigating, "decline")).toEqual(investigating);
  });

  it("starts over on again", () => {
    const fixed = tickUntilStill(step(tickUntilStill(START), "approve"));
    expect(step(fixed, "again")).toEqual(START);
  });

  it("shows the broken containers until the fix is checked", () => {
    const health = (phase: Parameters<typeof servicesAt>[0]) =>
      Object.fromEntries(servicesAt(phase).map((s) => [s.name, s.health]));
    expect(health("calm")).toMatchObject({ "shop-web": "ok", "shop-db": "ok" });
    expect(health("asks")).toMatchObject({ "shop-web": "down", "shop-db": "stopped" });
    expect(health("running")).toMatchObject({ "shop-web": "down", "shop-db": "stopped" });
    expect(health("checking")).toMatchObject({ "shop-web": "down", "shop-db": "ok" });
    expect(health("fixed")).toMatchObject({ "shop-web": "ok", "shop-db": "ok" });
  });
});
