import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { readLanguage, setLanguage, useT } from "@/lib/i18n";
import { LANGUAGE_SCRIPT } from "@/lib/i18n/script";

// The store keeps the last language in module state; a test of what a first load reads loads a
// fresh copy of it (without React hooks, which would then meet a second copy of React).
async function freshStore() {
  vi.resetModules();
  return import("@/lib/i18n");
}

beforeEach(() => {
  localStorage.clear();
});

describe("the language in force", () => {
  it("is English until one is known", async () => {
    const { readLanguage } = await freshStore();
    expect(readLanguage()).toBe("en");
  });

  it("is the stored one at once, before the settings have answered", async () => {
    localStorage.setItem("bleep:language", "de");
    const { readLanguage } = await freshStore();
    expect(readLanguage()).toBe("de");
  });

  it("ignores a stored value that is not a language", async () => {
    localStorage.setItem("bleep:language", "fr");
    const { readLanguage } = await freshStore();
    expect(readLanguage()).toBe("en");
  });

  it("is English when the storage cannot be read", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const { readLanguage } = await freshStore();
    expect(readLanguage()).toBe("en");
  });

  it("switches the page and its readers, and keeps the language for the next load", async () => {
    const { result } = renderHook(() => useT().nav.incidents);
    expect(result.current).toBe("Incidents");

    act(() => setLanguage("de"));

    expect(result.current).toBe("Vorfälle");
    expect(document.documentElement.lang).toBe("de");
    expect(localStorage.getItem("bleep:language")).toBe("de");
  });

  it("still switches when the storage refuses the write", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("private mode");
    });
    const { result } = renderHook(() => useT().nav.incidents);

    act(() => setLanguage("de"));

    expect(readLanguage()).toBe("de");
    expect(result.current).toBe("Vorfälle");
    expect(document.documentElement.lang).toBe("de");
  });

  it("sets the language in the head script from the stored pick", () => {
    localStorage.setItem("bleep:language", "de");
    document.documentElement.lang = "en";
    new Function(LANGUAGE_SCRIPT)();
    expect(document.documentElement.lang).toBe("de");

    localStorage.setItem("bleep:language", "xx");
    new Function(LANGUAGE_SCRIPT)();
    expect(document.documentElement.lang).toBe("de");
  });
});
