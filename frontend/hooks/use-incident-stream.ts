"use client";

import { useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";
import type { IncidentEvent } from "@/lib/api-types";

/** `loading` until the history is in; `live` while the stream is open; `reconnecting` between. */
export type StreamStatus = "loading" | "live" | "reconnecting";

export interface IncidentStream {
  events: IncidentEvent[];
  status: StreamStatus;
}

/** How long to wait before reopening a stream the browser gave up on (a 401, 404 or 5xx). */
export const RETRY_MS = 5_000;

/** `incoming` added to `current`: one event per seq, in seq order. Unchanged input is returned as is. */
export function mergeEvents(current: IncidentEvent[], incoming: IncidentEvent[]): IncidentEvent[] {
  const known = new Set(current.map((event) => event.seq));
  const fresh: IncidentEvent[] = [];
  for (const event of incoming) {
    if (!known.has(event.seq)) {
      known.add(event.seq);
      fresh.push(event);
    }
  }
  if (fresh.length === 0) return current;
  return [...current, ...fresh].sort((a, b) => a.seq - b.seq);
}

function parse(data: unknown): IncidentEvent | undefined {
  if (typeof data !== "string") return undefined;
  try {
    const event = JSON.parse(data) as IncidentEvent;
    return typeof event?.seq === "number" && typeof event.kind === "string" ? event : undefined;
  } catch {
    return undefined;
  }
}

/**
 * One incident's events: its history from `/events`, then live from `/api/stream`, opened after
 * the last seq it has. A dropped connection is retried by the browser with `Last-Event-ID`, so
 * the backend replays what was missed; a stream the browser gives up on is reopened after
 * `RETRY_MS` by reading the history again from the last seq. Every event is kept once by seq,
 * however the replays overlap.
 */
export function useIncidentStream(id: string): IncidentStream {
  const [state, setState] = useState<IncidentStream & { id: string }>({
    id,
    events: [],
    status: "loading",
  });
  // Another incident: start over (React's pattern for state that follows a prop).
  if (state.id !== id) setState({ id, events: [], status: "loading" });

  useEffect(() => {
    let active = true;
    let last = 0;
    let source: EventSource | undefined;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    const path = encodeURIComponent(id);

    const setStatus = (status: StreamStatus) => {
      if (active) setState((prev) => (prev.status === status ? prev : { ...prev, status }));
    };
    const add = (incoming: IncidentEvent[]) => {
      if (!active || incoming.length === 0) return;
      last = Math.max(last, ...incoming.map((event) => event.seq));
      setState((prev) => {
        const events = mergeEvents(prev.events, incoming);
        return events === prev.events ? prev : { ...prev, events };
      });
    };
    const later = () => {
      setStatus("reconnecting");
      retry = setTimeout(() => void connect(), RETRY_MS);
    };

    async function connect() {
      try {
        add(
          await apiFetch<IncidentEvent[]>(`/api/incidents/${path}/events?after=${last}`, {
            signal: controller.signal,
          }),
        );
      } catch {
        if (active) later(); // a lost session has already sent the browser to the login page
        return;
      }
      if (!active) return;
      const stream = new EventSource(`/api/stream?incident_id=${path}&after=${last}`);
      source = stream;
      stream.onopen = () => setStatus("live");
      stream.onmessage = (message) => {
        const event = parse(message.data);
        if (event) add([event]);
      };
      stream.onerror = () => {
        if (stream.readyState !== EventSource.CLOSED) {
          setStatus("reconnecting"); // the browser retries by itself, sending Last-Event-ID
          return;
        }
        stream.close();
        if (active) later();
      };
    }

    void connect();
    return () => {
      active = false;
      controller.abort();
      clearTimeout(retry);
      source?.close();
    };
  }, [id]);

  return { events: state.events, status: state.status };
}
