import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { modelSlots, resetModelSlots } from "@/lib/model-colours";

beforeEach(() => resetModelSlots());
afterEach(() => resetModelSlots());

describe("modelSlots", () => {
  it("gives each model its own slot, in the order they are first seen", () => {
    expect(modelSlots(["a", "b", "c"])).toEqual({ a: 1, b: 2, c: 3 });
  });

  it("keeps a model's slot when another range lists the models in another order", () => {
    modelSlots(["a", "b", "c"]);
    expect(modelSlots(["c", "a"])).toEqual({ c: 3, a: 1 });
    expect(modelSlots(["d", "b"])).toEqual({ d: 4, b: 2 });
  });

  it("leaves the ninth model without a slot, to be folded into other", () => {
    const models = Array.from({ length: 9 }, (_, i) => `m${i}`);
    const slots = modelSlots(models);
    expect(Object.values(slots).filter((slot) => slot === null)).toHaveLength(1);
    expect(slots.m8).toBeNull();
    expect(new Set(Object.values(slots).filter(Boolean)).size).toBe(8);
  });

  it("remembers the slots for the next visit", () => {
    modelSlots(["a", "b"]);
    expect(JSON.parse(localStorage.getItem("bleep.usage.model-colours") ?? "{}")).toEqual({
      a: 1,
      b: 2,
    });
  });

  it("ignores a stored entry that is not a slot", () => {
    localStorage.setItem("bleep.usage.model-colours", JSON.stringify({ a: 99, b: "x", c: 2 }));
    expect(modelSlots(["a", "c"])).toEqual({ a: 1, c: 2 });
  });
});
