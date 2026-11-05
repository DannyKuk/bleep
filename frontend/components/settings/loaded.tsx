"use client";

import { Loader2, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";
import { isStale, rereadFailed, SETTINGS_CHANGED, SettingsChanged } from "@/lib/settings-api";

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

export function Alert({ children }: { children: React.ReactNode }) {
  return (
    <p role="alert" className="flex items-center gap-2 text-sm text-health-down">
      <TriangleAlert aria-hidden className="size-4 shrink-0" />
      <span>{children}</span>
    </p>
  );
}

export function Status<T>({
  loaded,
  what,
  children,
}: {
  loaded: Loaded<T>;
  what: string;
  children: (data: T) => React.ReactNode;
}) {
  if (loaded.data !== undefined) return children(loaded.data);
  if (loaded.error) {
    return (
      <Alert>
        Could not load {what}: {loaded.error.message}
      </Alert>
    );
  }
  return (
    <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 aria-hidden className="size-4 animate-spin" />
      Loading {what}
    </p>
  );
}

/**
 * `saving(put, readAgain)` runs a save built from a view at some version. A stale one reads the
 * data again and is never retried, since its body would put back what changed elsewhere; `notice`
 * says so.
 */
export function useStaleSaving() {
  const [notice, setNotice] = useState<string | null>(null);
  async function saving(put: () => Promise<void>, readAgain: () => Promise<void>) {
    try {
      await put();
      setNotice(null);
    } catch (error) {
      if (!isStale(error)) throw error;
      try {
        await readAgain();
        setNotice(SETTINGS_CHANGED);
      } catch (failed) {
        setNotice(rereadFailed(failed));
      }
      throw new SettingsChanged();
    }
  }
  return { notice, setNotice, saving };
}
