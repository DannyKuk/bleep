import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { usePoll } from "@/hooks/use-poll";

function setVisibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
  document.dispatchEvent(new Event("visibilitychange"));
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

describe("usePoll", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    setVisibility("visible");
    vi.useRealTimers();
  });

  it("fetches now and every interval", async () => {
    const fetcher = vi.fn(async () => "x");
    renderHook(() => usePoll(fetcher, 10_000));
    await act(async () => {});
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => void vi.advanceTimersByTime(10_000));
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("reads at once on refresh and counts the next interval from there", async () => {
    const fetcher = vi.fn(async () => "x");
    const { result } = renderHook(() => usePoll(fetcher, 10_000));
    await act(async () => {});
    await act(async () => void vi.advanceTimersByTime(6_000));
    await act(async () => result.current.refresh());
    expect(fetcher).toHaveBeenCalledTimes(2);
    await act(async () => void vi.advanceTimersByTime(6_000));
    expect(fetcher).toHaveBeenCalledTimes(2);
    await act(async () => void vi.advanceTimersByTime(4_000));
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("pauses while hidden and refetches when visible again", async () => {
    const fetcher = vi.fn(async () => "x");
    renderHook(() => usePoll(fetcher, 10_000));
    await act(async () => {});
    act(() => setVisibility("hidden"));
    await act(async () => void vi.advanceTimersByTime(60_000));
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => setVisibility("visible"));
    expect(fetcher).toHaveBeenCalledTimes(2);
    await act(async () => void vi.advanceTimersByTime(10_000));
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("ignores a late older response", async () => {
    const first = deferred<string>();
    const second = deferred<string>();
    const fetcher = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const { result } = renderHook(() => usePoll<string>(fetcher, 10_000));
    await act(async () => void vi.advanceTimersByTime(10_000));
    await act(async () => second.resolve("new"));
    expect(result.current.data).toBe("new");
    await act(async () => first.resolve("old"));
    expect(result.current.data).toBe("new");
  });

  it("aborts the previous tick when a new one starts", async () => {
    const signals: AbortSignal[] = [];
    const fetcher = vi.fn((signal: AbortSignal) => {
      signals.push(signal);
      return new Promise<string>(() => {});
    });
    renderHook(() => usePoll(fetcher, 10_000));
    await act(async () => void vi.advanceTimersByTime(10_000));
    expect(signals[0].aborted).toBe(true);
    expect(signals[1].aborted).toBe(false);
  });

  it("aborts on unmount", async () => {
    let signal!: AbortSignal;
    const fetcher = vi.fn((s: AbortSignal) => {
      signal = s;
      return new Promise<string>(() => {});
    });
    const { unmount } = renderHook(() => usePoll(fetcher, 10_000));
    unmount();
    expect(signal.aborted).toBe(true);
  });
});
