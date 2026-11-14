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

  it("gives a new model a slot of a model that is not in the response when all eight are taken", () => {
    const old = Array.from({ length: 8 }, (_, i) => `old${i}`);
    modelSlots(old);
    expect(modelSlots(["new"])).toEqual({ new: 1 });
    expect(modelSlots(["new", "other"])).toEqual({ new: 1, other: 2 });
  });

  it("keeps the colour of every model still in the response when it frees slots", () => {
    const old = Array.from({ length: 8 }, (_, i) => `old${i}`);
    modelSlots(old);
    const slots = modelSlots(["old5", "new", "old2"]);
    expect(slots.old5).toBe(6);
    expect(slots.old2).toBe(3);
    expect(slots.new).toBe(1);
  });

  it("gives two models that come after many others two different slots", () => {
    modelSlots(Array.from({ length: 8 }, (_, i) => `old${i}`));
    const slots = modelSlots(["a", "b"]);
    expect(slots.a).not.toBeNull();
    expect(slots.b).not.toBeNull();
    expect(slots.a).not.toBe(slots.b);
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
