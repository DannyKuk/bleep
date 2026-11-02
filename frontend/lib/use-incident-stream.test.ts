import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { IncidentEvent } from "@/lib/api-types";
import { mergeEvents, RETRY_MS, useIncidentStream } from "@/lib/use-incident-stream";

function event(seq: number, kind = "notice"): IncidentEvent {
  return {
    incident_id: "inc-1",
    seq,
    kind,
    payload: { text: `event ${seq}` },
    at: "2026-11-02T18:00:00Z",
  };
}

/** Stands in for the browser's EventSource; tests drive it by hand. */
class FakeEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static instances: FakeEventSource[] = [];

  url: string;
  readyState = FakeEventSource.CONNECTING;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  open() {
    this.readyState = FakeEventSource.OPEN;
    this.onopen?.(new Event("open"));
  }

  send(...events: IncidentEvent[]) {
    for (const item of events) {
      this.onmessage?.(
        new MessageEvent("message", { data: JSON.stringify(item), lastEventId: String(item.seq) }),
      );
    }
  }

  /** A dropped connection: the browser retries by itself (CONNECTING) or gives up (CLOSED). */
  fail(readyState: number) {
    this.readyState = readyState;
    this.onerror?.(new Event("error"));
  }

  close() {
    this.readyState = FakeEventSource.CLOSED;
  }
}

function stubHistory(...replies: IncidentEvent[][]) {
  const fetchMock = vi.fn(async () => {
    const body = replies.length > 1 ? replies.shift() : replies[0];
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const seqs = (events: IncidentEvent[]) => events.map((item) => item.seq);
const latest = () => FakeEventSource.instances.at(-1)!;

describe("mergeEvents", () => {
  it("keeps one event per seq, in seq order", () => {
    const merged = mergeEvents([event(1), event(3)], [event(3), event(2), event(4), event(2)]);
    expect(seqs(merged)).toEqual([1, 2, 3, 4]);
  });

  it("returns the same array when nothing is new", () => {
    const current = [event(1), event(2)];
    expect(mergeEvents(current, [event(2)])).toBe(current);
  });
});

describe("useIncidentStream", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });
  afterEach(() => vi.useRealTimers());

  it("loads the history, then streams from the last seq it has", async () => {
    const fetchMock = stubHistory([event(1), event(2)]);
    const { result } = renderHook(() => useIncidentStream("inc-1"));
    expect(result.current.status).toBe("loading");
    await act(async () => {});

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/incidents/inc-1/events?after=0",
      expect.anything(),
    );
    expect(latest().url).toBe("/api/stream?incident_id=inc-1&after=2");
    act(() => latest().open());
    expect(result.current.status).toBe("live");
    act(() => latest().send(event(3)));
    expect(seqs(result.current.events)).toEqual([1, 2, 3]);
  });

  it("dedupes by seq when the browser reconnects and the stream replays", async () => {
    stubHistory([event(1), event(2)]);
    const { result } = renderHook(() => useIncidentStream("inc-1"));
    await act(async () => {});
    const source = latest();
    act(() => {
      source.open();
      source.send(event(2), event(3));
    });

    act(() => source.fail(FakeEventSource.CONNECTING));
    expect(result.current.status).toBe("reconnecting");
    // The browser reconnects with Last-Event-ID; an overlap is possible around the cursor.
    act(() => {
      source.open();
      source.send(event(3), event(4));
    });

    expect(result.current.status).toBe("live");
    expect(seqs(result.current.events)).toEqual([1, 2, 3, 4]);
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it("reopens a stream the browser gave up on, from the last seq, without duplicates", async () => {
    vi.useFakeTimers();
    const fetchMock = stubHistory([event(1), event(2)], [event(2), event(3), event(4)]);
    const { result } = renderHook(() => useIncidentStream("inc-1"));
    await act(async () => {});
    act(() => {
      latest().open();
      latest().send(event(3));
    });

    act(() => latest().fail(FakeEventSource.CLOSED));
    expect(result.current.status).toBe("reconnecting");
    await act(async () => void vi.advanceTimersByTime(RETRY_MS));

    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/incidents/inc-1/events?after=3",
      expect.anything(),
    );
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(latest().url).toBe("/api/stream?incident_id=inc-1&after=4");
    act(() => latest().send(event(4), event(5)));
    expect(seqs(result.current.events)).toEqual([1, 2, 3, 4, 5]);
  });

  it("retries when the history cannot be read", async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValue(
        new Response(JSON.stringify([event(1)]), {
          headers: { "content-type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useIncidentStream("inc-1"));
    await act(async () => {});
    expect(result.current.status).toBe("reconnecting");
    expect(FakeEventSource.instances).toHaveLength(0);

    await act(async () => void vi.advanceTimersByTime(RETRY_MS));
    expect(seqs(result.current.events)).toEqual([1]);
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it("ignores a message that is not an event", async () => {
    stubHistory([]);
    const { result } = renderHook(() => useIncidentStream("inc-1"));
    await act(async () => {});
    act(() => latest().onmessage?.(new MessageEvent("message", { data: "not json" })));
    expect(result.current.events).toEqual([]);
  });

  it("closes the stream on unmount", async () => {
    stubHistory([]);
    const { unmount } = renderHook(() => useIncidentStream("inc-1"));
    await act(async () => {});
    const source = latest();
    unmount();
    expect(source.readyState).toBe(FakeEventSource.CLOSED);
  });
});
