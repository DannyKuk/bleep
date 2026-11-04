"use client";

import { createContext, useContext, useMemo, type ReactNode } from "react";

import { usePoll } from "@/hooks/use-poll";
import { apiFetch } from "@/lib/api";
import type { HostView, Incident } from "@/lib/api-types";
import { overallState, type OverallState } from "@/lib/mood";

/** The newest incidents the header reads: the API's largest page. An unfinished incident is
 * nearly always among them, since the newest come first. */
export const OVERALL_INCIDENTS = 200;
const POLL_MS = 10_000;

export interface Overall {
  /** The newest incidents, newest first; undefined until the first read answers. */
  incidents: Incident[] | undefined;
  hosts: HostView[] | undefined;
  /** Red, amber or green; undefined until the first read answers. */
  state: OverallState | undefined;
  /** The last refresh failed; the data above is then the last good read. */
  error: Error | undefined;
  /** When the data was read (ms since the epoch), the "now" the state was computed for. */
  readAt: number | undefined;
  /** Reads the incidents and hosts again now, after a change the page made itself. */
  refresh: () => void;
}

async function fetchOverall(signal: AbortSignal) {
  const [incidents, hosts] = await Promise.all([
    apiFetch<Incident[]>(`/api/incidents?limit=${OVERALL_INCIDENTS}`, { signal }),
    apiFetch<HostView[]>("/api/hosts", { signal }),
  ]);
  return { incidents, hosts, readAt: Date.now() };
}

const OverallContext = createContext<Overall | null>(null);

/**
 * The one poll of `GET /api/incidents` and `GET /api/hosts` behind the header's Bleep: every
 * 10 s, paused while the tab is hidden (`usePoll`). It wraps the app layout, so any page reads it
 * with `useOverall()` instead of polling the same endpoints again.
 */
export function OverallProvider({ children }: { children: ReactNode }) {
  const { data, error, refresh } = usePoll(fetchOverall, POLL_MS);
  const value = useMemo<Overall>(
    () => ({
      incidents: data?.incidents,
      hosts: data?.hosts,
      state: data ? overallState(data.incidents, data.hosts, data.readAt) : undefined,
      error,
      readAt: data?.readAt,
      refresh,
    }),
    [data, error, refresh],
  );
  return <OverallContext.Provider value={value}>{children}</OverallContext.Provider>;
}

/** The shared overall state; only inside `OverallProvider` (the app layout). */
export function useOverall(): Overall {
  const overall = useContext(OverallContext);
  if (!overall) throw new Error("useOverall needs OverallProvider (the app layout) around it");
  return overall;
}
