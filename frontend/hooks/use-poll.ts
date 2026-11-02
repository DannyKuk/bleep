"use client";

import { useEffect, useState } from "react";

export interface Poll<T> {
  data: T | undefined;
  error: Error | undefined;
  loading: boolean;
}

/**
 * Runs `fetcher` now and every `intervalMs`, only while the tab is visible: a hidden tab stops
 * polling (and aborts what is in flight), and a tab that becomes visible refetches at once.
 * Each tick aborts the previous one and carries a sequence number, so a slow older response can
 * never overwrite newer data. Keeps the last good data when a refresh fails. `fetcher` must be
 * stable (module level or memoised). Until live events arrive (Task 23), this is the refresh.
 */
export function usePoll<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  intervalMs = 10_000,
): Poll<T> {
  const [state, setState] = useState<Poll<T>>({ data: undefined, error: undefined, loading: true });

  useEffect(() => {
    let active = true;
    let seq = 0;
    let controller: AbortController | undefined;
    let timer: ReturnType<typeof setInterval> | undefined;

    const tick = async () => {
      controller?.abort();
      const mine = ++seq;
      const current = new AbortController();
      controller = current;
      try {
        const data = await fetcher(current.signal);
        if (active && mine === seq) setState({ data, error: undefined, loading: false });
      } catch (error) {
        if (!active || mine !== seq || current.signal.aborted) return;
        setState((prev) => ({
          data: prev.data,
          error: error instanceof Error ? error : new Error(String(error)),
          loading: false,
        }));
      }
    };

    const start = () => {
      if (timer !== undefined) return;
      void tick();
      timer = setInterval(() => void tick(), intervalMs);
    };
    const stop = () => {
      clearInterval(timer);
      timer = undefined;
      controller?.abort();
    };
    const onVisibility = () => (document.visibilityState === "hidden" ? stop() : start());

    if (document.visibilityState !== "hidden") start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      active = false;
      document.removeEventListener("visibilitychange", onVisibility);
      stop();
    };
  }, [fetcher, intervalMs]);

  return state;
}
