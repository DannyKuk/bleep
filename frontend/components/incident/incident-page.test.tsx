import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  approval,
  detail,
  diagnosis,
  event,
  incident,
  report,
} from "@/components/incident/fixtures";
import { DETAIL_POLL_MS, IncidentPage } from "@/components/incident/incident-page";
import { subjectText } from "@/components/incident/incident-list";
import { PHONE_QUERY } from "@/components/incident/use-phone";
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
    expect(
      await screen.findByRole("heading", { name: "shop is in trouble. Can I fix it?" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("shop-web-1 died")[0]).toBeInTheDocument();
    expect(screen.getByText("Needs your yes")).toBeInTheDocument();
    expect(screen.getByText(/restart policy is no/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Yes, go ahead" })).toBeEnabled();
  });

  it("tells the story from the top: Bleep's mood, the progress, the plan waiting for you", async () => {
    stubBackend(detail());
    const { container } = render(<IncidentPage id="inc-1" />);
    const plan = await screen.findByRole("region", { name: "Here is my plan" });
    expect(container.querySelector("svg[data-mood]")).toHaveAttribute("data-mood", "needs_you");
    expect(container.querySelector("svg[data-mood]")).toHaveAttribute("width", "112");
    const headline = screen.getByRole("heading", { level: 1 });
    expect(within(headline).getByText("shop")).toHaveClass("font-mono");
    const progress = within(screen.getByRole("list", { name: "Progress" }));
    expect(progress.getByText("Your yes").closest("li")).toHaveAttribute("aria-current", "step");
    expect(plan).toHaveClass("border-accent");
    expect(screen.getByRole("region", { name: "What I found" })).toHaveClass("border-line");
    expect(screen.getByRole("region", { name: "How I got there" })).toBeInTheDocument();
    const timeline = within(screen.getByRole("list", { name: "Timeline" }));
    expect(timeline.getAllByRole("listitem")[0]).toHaveTextContent("shop-web-1 died");
  });

  it("shows the not-sure Bleep while an unsure diagnosis waits", async () => {
    stubBackend(detail({ diagnosis: diagnosis({ unsure: true }) }));
    const { container } = render(<IncidentPage id="inc-1" />);
    await screen.findByRole("region", { name: "Here is my plan" });
    expect(container.querySelector("svg[data-mood]")).toHaveAttribute("data-mood", "not_sure");
    expect(screen.getByRole("note")).toHaveTextContent("I am not sure");
  });

  it("asks for a vote on the diagnosis and the plan, showing the one already given", async () => {
    stubBackend(
      detail({
        feedback: [{ target: "diagnosis", vote: "down", note: null, at: "2026-11-02T18:10:00Z" }],
      }),
    );
    render(<IncidentPage id="inc-1" />);
    const diagnosisPanel = await screen.findByRole("region", { name: "What I found" });
    const proposalPanel = screen.getByRole("region", { name: "Here is my plan" });
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
    await screen.findByRole("heading", { name: "shop is in trouble. Can I fix it?" });
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
    const card = await screen.findByRole("article", { name: "Report" });
    expect(card).toHaveTextContent("Fixed and verified");
    expect(card).toHaveTextContent("Set restart: unless-stopped on shop-web.");
    expect(card).toHaveTextContent(subjectText(incident({ status: "resolved" })));
    expect(screen.queryByRole("region", { name: "Report" })).not.toBeInTheDocument();
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
    await screen.findByRole("button", { name: "Yes, go ahead" });
    expect(detailReads(fetchMock)).toBe(1);

    const send = (kind: string, payload: Record<string, unknown>) =>
      StubEventSource.last!.onmessage!({ data: JSON.stringify(event(kind, payload)) });
    await act(async () => send("tool_call", { name: "list_containers", args: {} }));
    expect(detailReads(fetchMock)).toBe(1);

    await act(async () => send("decision", { kind: "approve", text: null, via: "telegram" }));
    expect(detailReads(fetchMock)).toBe(2);
    expect(await screen.findByText(/You said yes on Telegram,/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Yes, go ahead" })).toBeDisabled();
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
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("I am looking into it.");

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

  describe("on a phone", () => {
    beforeEach(() =>
      vi.stubGlobal(
        "matchMedia",
        vi.fn((query: string) => ({
          matches: query === PHONE_QUERY,
          addEventListener: vi.fn(),
          removeEventListener: vi.fn(),
        })),
      ),
    );

    it("is one column: small header, Bleep at 76 px, what I found, the plan, the rest folded", async () => {
      stubBackend(detail());
      const { container } = render(<IncidentPage id="inc-1" />);
      const headline = await screen.findByRole("heading", {
        level: 1,
        name: "shop is in trouble. Can I fix it?",
      });
      expect(screen.getByRole("link", { name: "Back to incidents" })).toHaveAttribute(
        "href",
        "/incidents",
      );
      expect(container.querySelector("svg[data-mood]")).toHaveAttribute("width", "76");
      expect(screen.getByRole("button", { name: /^Theme:/ })).toBeInTheDocument();
      expect(screen.queryByRole("list", { name: "Progress" })).toBeNull();

      const found = screen.getByRole("region", { name: "What I found" });
      const plan = screen.getByRole("region", { name: "Here is my plan" });
      const following = (a: Element, b: Element) =>
        a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING;
      expect(following(headline, found)).toBeTruthy();
      expect(following(found, plan)).toBeTruthy();

      const folded = [...container.querySelectorAll("details")].filter((fold) =>
        /^(How I got there|Timeline)/.test(fold.querySelector("summary")?.textContent ?? ""),
      );
      expect(folded).toHaveLength(2);
      for (const fold of folded) expect(fold).not.toHaveAttribute("open");
    });

    it("pins the decision bar to the bottom with the same rules as on a desktop", async () => {
      const fetchMock = stubBackend(detail());
      const { container } = render(<IncidentPage id="inc-1" />);
      await screen.findByRole("button", { name: "Yes, go ahead" });
      const bar = container.querySelector("[data-pinned]") as HTMLElement;
      expect(bar).toHaveClass("fixed", "bottom-0");
      expect(within(bar).getByRole("button", { name: "Yes, go ahead" })).toHaveClass("h-14");
      expect(within(bar).getByRole("button", { name: "Tell Bleep something" })).toBeEnabled();

      fetchMock.mockImplementationOnce(async () =>
        json({ detail: "Already decided on Telegram", decided_via: "telegram" }, 409),
      );
      await userEvent.click(within(bar).getByRole("button", { name: "No, leave it" }));
      expect(await within(bar).findByRole("alert")).toHaveTextContent(
        "Already decided on Telegram",
      );
      expect(within(bar).queryAllByRole("button")).toHaveLength(0);
    });
  });

  describe("across the desktop breakpoint", () => {
    let phone = false;
    const listeners = new Set<() => void>();
    beforeEach(() => {
      phone = false;
      listeners.clear();
      vi.stubGlobal(
        "matchMedia",
        vi.fn((query: string) => ({
          get matches() {
            return query === PHONE_QUERY && phone;
          },
          addEventListener: (_: string, listener: () => void) => listeners.add(listener),
          removeEventListener: (_: string, listener: () => void) => listeners.delete(listener),
        })),
      );
    });
    const turn = async (toPhone: boolean) => {
      phone = toPhone;
      await act(async () => listeners.forEach((listener) => listener()));
    };

    it("keeps an answer in flight locked when the screen turns mid-answer", async () => {
      const answers: ((reply: Response) => void)[] = [];
      const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
        if (String(input).includes("/events")) return json([]);
        if (init?.method === "POST") return new Promise<Response>((done) => answers.push(done));
        return json(detail());
      });
      vi.stubGlobal("fetch", fetchMock);
      const { container } = render(<IncidentPage id="inc-1" />);
      await userEvent.click(await screen.findByRole("button", { name: "Yes, go ahead" }));
      expect(screen.getByRole("button", { name: "Yes, go ahead" })).toBeDisabled();

      await turn(true);
      const bar = container.querySelector("[data-pinned]") as HTMLElement;
      expect(container.querySelectorAll("[data-pinned]")).toHaveLength(1);
      for (const button of within(bar).getAllByRole("button")) expect(button).toBeDisabled();

      await act(async () =>
        answers[0](
          json({ kind: "approve", text: null, via: "dashboard", at: "2026-11-02T18:05:00Z" }),
        ),
      );
      expect(await within(bar).findByRole("status")).toHaveTextContent(
        /You said yes on the dashboard/,
      );
      expect(within(bar).queryAllByRole("button")).toHaveLength(0);
      await turn(false);
      expect(screen.getByRole("status")).toHaveTextContent(/You said yes on the dashboard/);
      expect(screen.getByRole("button", { name: "Yes, go ahead" })).toBeDisabled();
      const posts = fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");
      expect(posts).toHaveLength(1);
    });

    it("keeps a typed instruction when the screen turns", async () => {
      stubBackend(detail());
      const { container } = render(<IncidentPage id="inc-1" />);
      await userEvent.click(await screen.findByRole("button", { name: "Tell Bleep something" }));
      await userEvent.type(screen.getByRole("textbox"), "Restart only shop-api");

      await turn(true);
      const bar = container.querySelector("[data-pinned]") as HTMLElement;
      expect(within(bar).getByRole("textbox")).toHaveValue("Restart only shop-api");
      await turn(false);
      expect(screen.getByRole("textbox")).toHaveValue("Restart only shop-api");
      expect(container.querySelector("[data-pinned]")).toBeNull();
    });
  });
});
