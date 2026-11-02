"use client";

import { useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";

export interface Poll<T> {
  data: T | undefined;
  error: Error | undefined;
  loading: boolean;
}

/**
 * GETs `path` now and every `intervalMs`. Keeps the last good data when a refresh fails, so a
 * blip does not blank the dashboard. Until live events arrive (Task 23), this is the refresh.
 */
export function usePoll<T>(path: string, intervalMs = 10_000): Poll<T> {
  const [state, setState] = useState<Poll<T>>({ data: undefined, error: undefined, loading: true });

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const data = await apiFetch<T>(path);
        if (active) setState({ data, error: undefined, loading: false });
      } catch (error) {
        if (active) {
          setState((prev) => ({
            data: prev.data,
            error: error instanceof Error ? error : new Error(String(error)),
            loading: false,
          }));
        }
      }
    };
    void load();
    const timer = setInterval(load, intervalMs);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [path, intervalMs]);

  return state;
}
