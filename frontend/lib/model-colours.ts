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
 * The chart colour slot (1 to 8) of each model, or null for the ninth and later, which a chart
 * folds into "Other". A model takes the first free slot when it is first seen and keeps it, so
 * a change of range, or a model that stops being called, never repaints the others; the
 * assignment is kept in this browser for the next visit.
 */
export function modelSlots(models: readonly string[]): Record<string, number | null> {
  const slots = load();
  const used = new Set(Object.values(slots));
  let changed = false;
  for (const model of models) {
    if (model in slots) continue;
    const free = Array.from({ length: SERIES_SLOTS }, (_, i) => i + 1).find((n) => !used.has(n));
    if (free === undefined) continue;
    slots[model] = free;
    used.add(free);
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
