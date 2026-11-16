"use client";

import { useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";

export interface Loaded<T> {
  data?: T;
  error?: Error;
}

/** One GET, read on mount; `set` replaces the data with what a save returned, or updates it. */
export function useLoaded<T>(path: string) {
  const [state, setState] = useState<Loaded<T>>({});
  useEffect(() => {
    const controller = new AbortController();
    apiFetch<T>(path, { signal: controller.signal })
      .then((data) => setState({ data }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({ error: error instanceof Error ? error : new Error(String(error)) });
      });
    return () => controller.abort();
  }, [path]);
  const set = (next: T | ((data: T | undefined) => T)) =>
    setState((now) => ({
      data: typeof next === "function" ? (next as (data: T | undefined) => T)(now.data) : next,
    }));
  return { ...state, set };
}
