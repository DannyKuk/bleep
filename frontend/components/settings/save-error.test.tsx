import { describe, expect, it } from "vitest";

import { problemsOf } from "@/components/settings/save-error";
import { ApiError } from "@/lib/api";

const refused = (detail: unknown) => new ApiError(422, "refused", undefined, { detail });

describe("problemsOf", () => {
  it("names a field in a list by its label and its number", () => {
    const error = refused([
      { loc: ["hosts", "m1", "rules", 1], msg: "String should have at most 500 characters" },
      { loc: ["hosts", "m1", "services", "s", "urls", 0, "expect_status"], msg: "Input bad" },
    ]);
    expect(problemsOf(error, { rules: "Rule", expect_status: "Expected status" })).toEqual([
      "Rule 2: String should have at most 500 characters",
      "Expected status 1: Input bad",
    ]);
  });

  it("keeps Bleep's own sentence and an unknown field's message", () => {
    expect(problemsOf(new ApiError(422, "no host 'ghost' in the hosts file"))).toEqual([
      "no host 'ghost' in the hosts file",
    ]);
    expect(problemsOf(refused([{ loc: ["x"], msg: "Value error, nope" }]))).toEqual(["nope"]);
    expect(problemsOf(new TypeError("Failed to fetch"))).toEqual(["Failed to fetch"]);
  });
});
