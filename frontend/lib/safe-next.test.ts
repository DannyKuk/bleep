import { describe, expect, it } from "vitest";

import { safeNext } from "@/lib/safe-next";

describe("safeNext", () => {
  it.each([
    ["/", "/"],
    ["/incidents", "/incidents"],
    ["/incidents/inc_1?tab=a#top", "/incidents/inc_1?tab=a#top"],
  ])("keeps the same-site path %s", (input, expected) => {
    expect(safeNext(input)).toBe(expected);
  });

  it.each([
    ["tab", "/\t/evil.com"],
    ["newline", "/\n/evil.com"],
    ["double slash", "//evil.com"],
    ["backslash", "/\\evil.com"],
    ["slash backslash", "/\\/evil.com"],
    ["https", "https://evil.com/x"],
    ["javascript", "javascript:alert(1)"],
    ["empty", ""],
  ])("rejects %s", (_name, input) => {
    expect(safeNext(input)).toBe("/");
  });
});
