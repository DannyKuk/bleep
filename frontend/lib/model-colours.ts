export const SERIES_SLOTS = 8;

const KEY = "bleep.usage.model-colours";

let held: Record<string, number> | null = null;

function load(): Record<string, number> {
  if (held) return held;
  held = {};
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(KEY) ?? "{}");
    if (parsed && typeof parsed === "object") {
      for (const [model, slot] of Object.entries(parsed)) {
        if (Number.isInteger(slot) && slot >= 1 && slot <= SERIES_SLOTS) held[model] = slot;
      }
    }
  } catch {
    // no storage or a damaged entry: the colours last until reload
  }
  return held;
}

/**
 * The chart colour slot (1 to 8) of each of `models`, the models of the response on screen, or
 * null for the ninth of them and later, which a chart folds into "Other". A model takes a free
 * slot when it is first seen and keeps it, so a change of range never repaints the models still
 * in it. A slot held by a model that is not in `models` is taken back only when no other slot is
 * free, so a model that comes back usually has its colour again, and a browser that has seen more
 * than eight models over time still colours the eight on screen. The assignment is kept in this
 * browser for the next visit.
 */
export function modelSlots(models: readonly string[]): Record<string, number | null> {
  const slots = load();
  const present = new Set(models);
  let changed = false;
  for (const model of models) {
    if (model in slots) continue;
    const taken = new Set(Object.values(slots));
    let free = Array.from({ length: SERIES_SLOTS }, (_, i) => i + 1).find((n) => !taken.has(n));
    if (free === undefined) {
      const absent = Object.entries(slots)
        .filter(([other]) => !present.has(other))
        .sort(([, a], [, b]) => a - b)[0];
      if (absent === undefined) continue;
      free = absent[1];
      delete slots[absent[0]];
    }
    slots[model] = free;
    changed = true;
  }
  if (changed) {
    try {
      localStorage.setItem(KEY, JSON.stringify(slots));
    } catch {
      // private mode
    }
  }
  return Object.fromEntries(models.map((model) => [model, slots[model] ?? null]));
}

/** Forgets every assignment; for tests. */
export function resetModelSlots(): void {
  held = null;
  try {
    localStorage.removeItem(KEY);
  } catch {
    // nothing stored
  }
}
