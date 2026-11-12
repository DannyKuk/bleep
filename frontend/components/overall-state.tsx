"use client";

import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";

import { usePoll } from "@/hooks/use-poll";
import { apiFetch } from "@/lib/api";
import type { HostView, Incident, ServiceView } from "@/lib/api-types";
import { overallMood, overallState, type Mood, type OverallState } from "@/lib/mood";

/** The newest incidents the header reads: the API's largest page. An unfinished incident is
 * nearly always among them, since the newest come first. */
export const OVERALL_INCIDENTS = 200;
const POLL_MS = 10_000;

export interface Overall {
  /** The newest incidents, newest first; undefined until the first read answers. */
  incidents: Incident[] | undefined;
  hosts: HostView[] | undefined;
  /** Every service, read on a poll of its own; undefined until its first read answers. */
  services: ServiceView[] | undefined;
  /** Red, amber or green; undefined until the first read answers. */
  state: OverallState | undefined;
  /** Bleep's overall mood from the same read, judged without the waiting plans' details. */
  mood: Mood | undefined;
  /** The last refresh failed; the data above is then the last good read. */
  error: Error | undefined;
  /** The last read of the services failed; `services` is then the last good read. */
  servicesError: Error | undefined;
  /** When the data was read (ms since the epoch), the "now" the state was computed for. */
  readAt: number | undefined;
  /** Reads the incidents, hosts and services again now, after a change the page made itself. */
  refresh: () => void;
}

async function fetchOverall(signal: AbortSignal) {
  const [incidents, hosts] = await Promise.all([
    apiFetch<Incident[]>(`/api/incidents?limit=${OVERALL_INCIDENTS}`, { signal }),
    apiFetch<HostView[]>("/api/hosts", { signal }),
  ]);
  return { incidents, hosts, readAt: Date.now() };
}

const fetchServices = (signal: AbortSignal) => apiFetch<ServiceView[]>("/api/services", { signal });

const OverallContext = createContext<Overall | null>(null);

/**
 * The one poll of `GET /api/incidents` and `GET /api/hosts` behind the header's Bleep, and one of
 * `GET /api/services` beside it: every 10 s, paused while the tab is hidden (`usePoll`). The
 * services are a poll of their own so that a failed read of them leaves the header's incidents
 * and hosts standing. Until their first read answers the state is judged without them; after a
 * failed read it is judged on the last good list, as the incidents and hosts are. It wraps the app
 * layout, so any page reads it with `useOverall()` instead of polling the same endpoints again.
 */
export function OverallProvider({ children }: { children: ReactNode }) {
  const { data, error, refresh: refreshOverall } = usePoll(fetchOverall, POLL_MS);
  const services = usePoll(fetchServices, POLL_MS);
  const refreshServices = services.refresh;
  const refresh = useCallback(() => {
    refreshOverall();
    refreshServices();
  }, [refreshOverall, refreshServices]);
  const value = useMemo<Overall>(
    () => ({
      incidents: data?.incidents,
      hosts: data?.hosts,
      services: services.data,
      state: data
        ? overallState(data.incidents, data.hosts, data.readAt, services.data)
        : undefined,
      mood: data
        ? overallMood(data.incidents, data.hosts, new Set(), data.readAt, services.data)
        : undefined,
      error,
      servicesError: services.error,
      readAt: data?.readAt,
      refresh,
    }),
    [data, error, services.data, services.error, refresh],
  );
  return <OverallContext.Provider value={value}>{children}</OverallContext.Provider>;
}

/** The shared overall state; only inside `OverallProvider` (the app layout). */
export function useOverall(): Overall {
  const overall = useContext(OverallContext);
  if (!overall) throw new Error("useOverall needs OverallProvider (the app layout) around it");
  return overall;
}
