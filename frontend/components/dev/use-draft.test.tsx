import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useDraft } from "@/components/dev/section";

type Form = { a: string; b: string };

function setup(fresh: Form) {
  return renderHook(({ view }) => useDraft(view), { initialProps: { view: fresh } });
}

describe("useDraft", () => {
  it("starts as the values in force", () => {
    const { result } = setup({ a: "1", b: "2" });
    expect(result.current[0]).toEqual({ a: "1", b: "2" });
  });

  it("takes every field from the new view once an edit is reverted", () => {
    const { result, rerender } = setup({ a: "1", b: "2" });
    act(() => result.current[1]({ a: "9", b: "2" }));
    act(() => result.current[1]({ a: "1", b: "2" }));

    rerender({ view: { a: "5", b: "2" } });

    expect(result.current[0]).toEqual({ a: "5", b: "2" });
  });

  it("keeps an edit over a changed view and takes the other fields from it", () => {
    const { result, rerender } = setup({ a: "1", b: "2" });
    act(() => result.current[1]({ a: "9", b: "2" }));

    rerender({ view: { a: "5", b: "7" } });

    expect(result.current[0]).toEqual({ a: "9", b: "7" });
  });

  it("follows a save that changes the view, edited field included", () => {
    const { result, rerender } = setup({ a: "1", b: "2" });
    act(() => result.current[1]({ a: "9", b: "2" }));
    const saved = { a: "9", b: "3" };
    act(() => result.current[1](saved));

    rerender({ view: saved });

    expect(result.current[0]).toEqual(saved);
    rerender({ view: { a: "9", b: "4" } });
    expect(result.current[0]).toEqual({ a: "9", b: "4" });
  });
});
