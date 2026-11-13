"use client";

import { Loader2, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { isStale, SettingsChanged } from "@/lib/settings-api";

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
  what: "settings" | "plugins" | "secrets" | "telegram" | "docs";
  children: (data: T) => React.ReactNode;
}) {
  const t = useT().settings.load[what];
  if (loaded.data !== undefined) return children(loaded.data);
  if (loaded.error) return <Alert>{t.failed(loaded.error.message)}</Alert>;
  return (
    <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 aria-hidden className="size-4 animate-spin" />
      {t.loading}
    </p>
  );
}

/**
 * `saving(put, readAgain)` runs a save built from a view at some version. A stale one reads the
 * data again and is never retried, since its body would put back what changed elsewhere; `notice`
 * says so.
 */
export function useStaleSaving() {
  const t = useT().settings;
  const [notice, setNotice] = useState<string | null>(null);
  async function saving(put: () => Promise<void>, readAgain: () => Promise<void>) {
    try {
      await put();
      setNotice(null);
    } catch (error) {
      if (!isStale(error)) throw error;
      try {
        await readAgain();
        setNotice(t.changedElsewhere);
      } catch (failed) {
        setNotice(t.rereadFailed(failed instanceof Error ? failed.message : String(failed)));
      }
      throw new SettingsChanged(t.notSaved);
    }
  }
  return { notice, setNotice, saving };
}
