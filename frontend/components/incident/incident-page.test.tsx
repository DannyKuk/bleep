import { act, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { approval, detail, event, incident, report } from "@/components/incident/fixtures";
import { DETAIL_POLL_MS, IncidentPage } from "@/components/incident/incident-page";
import type { IncidentDetail } from "@/lib/api-types";

class StubEventSource {
  static readonly CLOSED = 2;
  static last: StubEventSource | undefined;
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((message: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    StubEventSource.last = this;
  }
  close() {
    this.readyState = StubEventSource.CLOSED;
  }
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** Serves the incident's detail (one reply per read, the last repeating) and an empty history. */
function stubBackend(...details: (IncidentDetail | Response)[]) {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input);
    if (url.includes("/events")) return json([]);
    const next = details.length > 1 ? details.shift()! : details[0];
    return next instanceof Response ? next : json(next);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const detailReads = (fetchMock: ReturnType<typeof stubBackend>) =>
  fetchMock.mock.calls.filter(([input]) => String(input) === "/api/incidents/inc-1").length;

describe("IncidentPage", () => {
  beforeEach(() => vi.stubGlobal("EventSource", StubEventSource));
  afterEach(() => vi.useRealTimers());

  it("shows the incident with its diagnosis and the plan waiting for a decision", async () => {
    stubBackend(detail());
    render(<IncidentPage id="inc-1" />);
    expect(await screen.findByRole("heading", { name: "shop-web-1 died" })).toBeInTheDocument();
    expect(screen.getByText("Needs your yes")).toBeInTheDocument();
    expect(screen.getByText(/restart policy is no/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
  });

  it("asks for a vote on the diagnosis and the plan, showing the one already given", async () => {
    stubBackend(
      detail({
        feedback: [{ target: "diagnosis", vote: "down", note: null, at: "2026-11-02T18:10:00Z" }],
      }),
    );
    render(<IncidentPage id="inc-1" />);
    const diagnosisPanel = await screen.findByRole("region", { name: "Diagnosis" });
    const proposalPanel = screen.getByRole("region", { name: "Proposal" });
    expect(diagnosisPanel).toHaveTextContent("Was this diagnosis right?");
    expect(proposalPanel).toHaveTextContent("Was this plan right?");
    expect(within(diagnosisPanel).getByRole("button", { name: "Wrong" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("link", { name: "demo/compose:shop" })).toHaveAttribute(
      "href",
      "/services/demo%2Fcompose%3Ashop",
    );
  });

  it("asks for no vote before there is a diagnosis or a plan", async () => {
    stubBackend(detail({ diagnosis: null, proposal: null, approval: null }));
    render(<IncidentPage id="inc-1" />);
    await screen.findByRole("heading", { name: "shop-web-1 died" });
    expect(screen.queryByText(/Was this .* right\?/)).not.toBeInTheDocument();
  });

  it("shows the report once the run has written it", async () => {
    stubBackend(
      detail({
        incident: incident({ status: "resolved" }),
        outcome: "resolved",
        report: report(),
      }),
    );
    render(<IncidentPage id="inc-1" />);
    const panel = await screen.findByRole("region", { name: "Report" });
    expect(panel).toHaveTextContent("Fixed and verified");
    expect(panel).toHaveTextContent("Set restart: unless-stopped on shop-web.");
  });

  it("reads the incident again when a step event arrives, not on tool events", async () => {
    const decidedOnTelegram = detail({
      approval: approval({
        status: "approved",
        decision: { kind: "approve", text: null, via: "telegram", at: "2026-11-02T18:05:00Z" },
      }),
    });
    const fetchMock = stubBackend(detail(), decidedOnTelegram);
    render(<IncidentPage id="inc-1" />);
    await screen.findByRole("button", { name: "Approve" });
    expect(detailReads(fetchMock)).toBe(1);

    const send = (kind: string, payload: Record<string, unknown>) =>
      StubEventSource.last!.onmessage!({ data: JSON.stringify(event(kind, payload)) });
    await act(async () => send("tool_call", { name: "list_containers", args: {} }));
    expect(detailReads(fetchMock)).toBe(1);

    await act(async () => send("decision", { kind: "approve", text: null, via: "telegram" }));
    expect(detailReads(fetchMock)).toBe(2);
    expect(await screen.findByText(/Approved on Telegram,/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
  });

  it("reads the incident again on an interval, for status changes that publish no event", async () => {
    vi.useFakeTimers();
    const fetchMock = stubBackend(
      detail({ incident: incident({ status: "investigating" }) }),
      detail(),
    );
    render(<IncidentPage id="inc-1" />);
    await act(async () => {});
    expect(screen.getByText("Investigating")).toBeInTheDocument();

    await act(async () => void vi.advanceTimersByTime(DETAIL_POLL_MS));
    expect(detailReads(fetchMock)).toBe(2);
    expect(screen.getByText("Needs your yes")).toBeInTheDocument();
  });

  it("says so when there is no such incident", async () => {
    stubBackend(json({ detail: "no such incident" }, 404));
    render(<IncidentPage id="inc-1" />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "There is no incident with this id.",
    );
  });
});
