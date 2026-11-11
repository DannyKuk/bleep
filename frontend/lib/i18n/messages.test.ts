import { describe, expect, it } from "vitest";

import { MESSAGES } from "@/lib/i18n";

type Tree = { [key: string]: unknown };

/** Every message's path, with what it is: words, a sentence taking `n` values, or a pair. */
function shape(tree: Tree, path = ""): Record<string, string> {
  const found: Record<string, string> = {};
  for (const [key, value] of Object.entries(tree)) {
    const at = path ? `${path}.${key}` : key;
    if (typeof value === "string") found[at] = "words";
    else if (typeof value === "function") found[at] = `takes ${value.length}`;
    else if (Array.isArray(value)) found[at] = `${value.length} words`;
    else Object.assign(found, shape(value as Tree, at));
  }
  return found;
}

/** Every string a message holds, a sentence's filled in with sample values. */
function texts(tree: Tree): string[] {
  return Object.values(tree).flatMap((value): string[] => {
    if (typeof value === "string") return [value];
    if (typeof value === "function") {
      const samples = [3, 2, "x"];
      return [String(value(...samples.slice(0, value.length)))];
    }
    if (Array.isArray(value)) return value.map(String);
    return texts(value as Tree);
  });
}

const SAMPLES = [111, 222, 333];

/** For every sentence taking values, which of them it writes out (by position). */
function usedValues(tree: Tree, path = ""): Record<string, number[]> {
  const found: Record<string, number[]> = {};
  for (const [key, value] of Object.entries(tree)) {
    const at = path ? `${path}.${key}` : key;
    if (typeof value === "function") {
      const text = String(value(...SAMPLES.slice(0, value.length)));
      found[at] = SAMPLES.slice(0, value.length).flatMap((n, i) =>
        text.includes(String(n)) ? [i] : [],
      );
    } else if (typeof value === "object" && value !== null && !Array.isArray(value)) {
      Object.assign(found, usedValues(value as Tree, at));
    }
  }
  return found;
}

describe("messages", () => {
  // The type check holds every key too; this also covers the maps typed by a plain string key.
  it("has every key in both languages, each taking the same values", () => {
    expect(shape(MESSAGES.de)).toEqual(shape(MESSAGES.en));
  });

  it("has every sentence write out the same values in both languages", () => {
    expect(usedValues(MESSAGES.de)).toEqual(usedValues(MESSAGES.en));
  });

  it.each(["en", "de"] as const)("has no empty message in %s", (language) => {
    for (const text of texts(MESSAGES[language])) expect(text.trim()).not.toBe("");
  });
});
