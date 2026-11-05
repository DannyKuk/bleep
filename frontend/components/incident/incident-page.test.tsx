import { act, render, screen, waitFor, within } from "@testing-library/react";
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
import { OverallProvider } from "@/components/overall-state";
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

let hostList: { id: string; name: string }[] = [];

/** The header's overall poll (`OverallProvider`): the newest incidents and the hosts. */
function answerOverall(input: RequestInfo | URL): Response | undefined {
  const url = String(input);
  if (url === "/api/hosts") return json(hostList);
  if (url.startsWith("/api/incidents?limit=")) return json([]);
  return undefined;
}

function renderPage() {
  return render(
    <OverallProvider>
      <IncidentPage id="inc-1" />
    </OverallProvider>,
  );
}

/** Serves the incident's detail (one reply per read, the last repeating) and an empty history. */
function stubBackend(...details: (IncidentDetail | Response)[]) {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const overallReply = answerOverall(input);
    if (overallReply) return overallReply;
    const url = String(input);
    if (url.includes("/events")) return json([]);
    const next = details.length > 1 ? details.shift()! : details[0];
    return next instanceof Response ? next : json(next);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const hostReads = (fetchMock: ReturnType<typeof stubBackend>) =>
  fetchMock.mock.calls.filter(([input]) => String(input) === "/api/hosts").length;

const detailReads = (fetchMock: ReturnType<typeof stubBackend>) =>
  fetchMock.mock.calls.filter(([input]) => String(input) === "/api/incidents/inc-1").length;

describe("IncidentPage", () => {
  beforeEach(() => {
    vi.stubGlobal("EventSource", StubEventSource);
    hostList = [{ id: "demo", name: "Demo stack" }];
  });
  afterEach(() => vi.useRealTimers());

  it("shows the incident with its diagnosis and the plan waiting for a decision", async () => {
    stubBackend(detail());
    renderPage();
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
    const { container } = renderPage();
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
    const { container } = renderPage();
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
    renderPage();
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
    renderPage();
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
    renderPage();
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
    renderPage();
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
    renderPage();
    await act(async () => {});
    expect(screen.getByText("Investigating")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("I am looking into it.");

    await act(async () => void vi.advanceTimersByTime(DETAIL_POLL_MS));
    expect(detailReads(fetchMock)).toBe(2);
    expect(screen.getByText("Needs your yes")).toBeInTheDocument();
  });

  it("says so when there is no such incident", async () => {
    stubBackend(json({ detail: "no such incident" }, 404));
    renderPage();
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
      const { container } = renderPage();
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
      const { container } = renderPage();
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
        const overallReply = answerOverall(input);
        if (overallReply) return overallReply;
        if (String(input).includes("/events")) return json([]);
        if (init?.method === "POST") return new Promise<Response>((done) => answers.push(done));
        return json(detail());
      });
      vi.stubGlobal("fetch", fetchMock);
      const { container } = renderPage();
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
      const { container } = renderPage();
      await userEvent.click(await screen.findByRole("button", { name: "Tell Bleep something" }));
      await userEvent.type(screen.getByRole("textbox"), "Restart only shop-api");

      await turn(true);
      const bar = container.querySelector("[data-pinned]") as HTMLElement;
      expect(within(bar).getByRole("textbox")).toHaveValue("Restart only shop-api");
      await turn(false);
      expect(screen.getByRole("textbox")).toHaveValue("Restart only shop-api");
      expect(container.querySelector("[data-pinned]")).toBeNull();
    });

    it("keeps a feedback note and a vote in flight when the screen turns", async () => {
      const votes: ((reply: Response) => void)[] = [];
      vi.stubGlobal(
        "fetch",
        vi.fn<typeof fetch>(async (input, init) => {
          const overallReply = answerOverall(input);
          if (overallReply) return overallReply;
          if (String(input).includes("/events")) return json([]);
          if (init?.method === "POST") return new Promise<Response>((done) => votes.push(done));
          return json(detail());
        }),
      );
      renderPage();
      const found = () => screen.getByRole("region", { name: "What I found" });
      await within(await screen.findByRole("region", { name: "What I found" })).findByRole(
        "button",
        { name: "Add a note" },
      );
      await userEvent.click(within(found()).getByRole("button", { name: "Add a note" }));
      await userEvent.type(within(found()).getByRole("textbox"), "It was the proxy");

      await turn(true);
      expect(within(found()).getByRole("textbox")).toHaveValue("It was the proxy");
      await userEvent.click(within(found()).getByRole("button", { name: "Wrong" }));
      expect(within(found()).getByRole("button", { name: "Wrong" })).toBeDisabled();

      await turn(false);
      expect(within(found()).getByRole("textbox")).toHaveValue("It was the proxy");
      expect(within(found()).getByRole("button", { name: "Wrong" })).toBeDisabled();
      await act(async () =>
        votes[0](
          json({
            target: "diagnosis",
            vote: "down",
            note: "It was the proxy",
            at: "2026-11-02T18:10:00Z",
          }),
        ),
      );
      expect(await within(found()).findByRole("status")).toHaveTextContent("Marked wrong");
      expect(votes).toHaveLength(1);
    });
  });

  describe("repeats", () => {
    it("a repeat links the first time it happened", async () => {
      stubBackend(detail({ incident: incident({ fold_of: "inc-first" }), repeats: 3 }));
      render(<IncidentPage id="inc-1" />);
      expect(await screen.findByRole("note")).toHaveTextContent("this happened 3 times in all");
      expect(screen.getByRole("link", { name: "See the first time" })).toHaveAttribute(
        "href",
        "/incidents/inc-first",
      );
    });

    it("the first incident lists its repeats, newest first", async () => {
      const fetchMock = vi.fn<typeof fetch>(async (input) => {
        const url = String(input);
        if (url.includes("fold_of=inc-1")) {
          return json([
            {
              ...incident({ id: "inc-3", status: "closed", fold_of: "inc-1" }),
              votes: {},
              repeats: 3,
            },
            {
              ...incident({ id: "inc-2", status: "closed", fold_of: "inc-1" }),
              votes: {},
              repeats: 3,
            },
          ]);
        }
        if (url.includes("/events")) return json([]);
        return json(detail({ repeats: 3 }));
      });
      vi.stubGlobal("fetch", fetchMock);
      render(<IncidentPage id="inc-1" />);
      const repeats = await screen.findByRole("region", { name: "Repeats" });
      expect(repeats).toHaveTextContent("Seen 3 times");
      const links = await within(repeats).findAllByRole("link");
      expect(links.map((link) => link.getAttribute("href"))).toEqual([
        "/incidents/inc-3",
        "/incidents/inc-2",
      ]);
    });
  });

  describe("This is expected", () => {
    const SENTENCE = "fetch on shop is expected to exit: I stay quiet when it exits with 0.";

    /** The overall poll, the detail with `expectation`, and the POST's reply. */
    function stubExpected(post: Response) {
      const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
        const overallReply = answerOverall(input);
        if (overallReply) return overallReply;
        if (init?.method === "POST") return post.clone();
        if (String(input).includes("/events")) return json([]);
        return json(detail({ expectation: SENTENCE }));
      });
      vi.stubGlobal("fetch", fetchMock);
      return fetchMock;
    }

    it("is offered with what it records, posts, and reads the incident and the header again", async () => {
      const fetchMock = stubExpected(json({ text: `You said this is expected. ${SENTENCE}` }));
      renderPage();
      expect(await screen.findByText(SENTENCE)).toBeInTheDocument();
      await waitFor(() => expect(hostReads(fetchMock)).toBe(1));
      await userEvent.click(screen.getByRole("button", { name: "This is expected" }));
      await waitFor(() => expect(detailReads(fetchMock)).toBeGreaterThan(1));
      const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST")!;
      expect(String(post[0])).toBe("/api/incidents/inc-1/expected");
      await waitFor(() => expect(hostReads(fetchMock)).toBe(2));
    });

    it("lets a long sentence wrap instead of widening a phone's page", async () => {
      stubExpected(json({ text: "" }));
      renderPage();
      const sentence = await screen.findByText(SENTENCE);
      expect(sentence).toHaveClass("wrap-anywhere", "min-w-0");
      expect(sentence.parentElement).toHaveClass("min-w-0", "max-w-full");
    });

    it("is not offered when the API says nothing can be expected", async () => {
      stubBackend(detail({ expectation: null }));
      renderPage();
      await screen.findByRole("heading", { level: 1 });
      expect(screen.queryByRole("button", { name: "This is expected" })).toBeNull();
    });

    it("shows the API's words when a decision elsewhere won", async () => {
      stubExpected(json({ detail: "Already decided on Telegram; nothing was recorded." }, 409));
      renderPage();
      await userEvent.click(await screen.findByRole("button", { name: "This is expected" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("Already decided on Telegram");
    });
  });

  describe("I'm on it", () => {
    const subject = (kind: "service" | "host") =>
      kind === "service"
        ? { kind, host_id: "demo", service_id: "demo/compose:shop" }
        : { kind, host_id: "demo", service_id: null };

    function stubWithPut() {
      const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
        const overallReply = answerOverall(input);
        if (overallReply) return overallReply;
        const url = String(input);
        if (init?.method === "PUT") return json({});
        if (url.includes("/events")) return json([]);
        return json(detail());
      });
      vi.stubGlobal("fetch", fetchMock);
      return fetchMock;
    }

    it("an awaiting incident offers I'm on it; Leave it alone PUTs for its service", async () => {
      const fetchMock = stubWithPut();
      renderPage();
      await userEvent.click(await screen.findByRole("button", { name: "I'm on it" }));
      await userEvent.click(await screen.findByRole("menuitem", { name: /Leave it alone/ }));
      await waitFor(() => expect(detailReads(fetchMock)).toBeGreaterThan(1));
      const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT")!;
      expect(String(put[0])).toBe("/api/quiet/services/demo/compose%3Ashop");
      expect(JSON.parse(String(put[1]?.body))).toEqual({ mode: "leave_alone" });
    });

    it.each(["executing", "verifying", "resolved", "closed", "failed"] as const)(
      "a %s incident offers no I'm on it",
      async (status) => {
        stubBackend(detail({ incident: incident({ status }) }));
        renderPage();
        await screen.findByRole("heading", { level: 1 });
        expect(screen.queryByRole("button", { name: "I'm on it" })).toBeNull();
      },
    );

    it("a quiet choice refreshes the header's overall state", async () => {
      const fetchMock = stubWithPut();
      renderPage();
      await userEvent.click(await screen.findByRole("button", { name: "I'm on it" }));
      await userEvent.click(await screen.findByRole("menuitem", { name: /Leave it alone/ }));
      await waitFor(() => expect(hostReads(fetchMock)).toBe(2));
    });

    it("a service incident without a service id offers no I'm on it", async () => {
      stubBackend(
        detail({
          incident: incident({ subject: { kind: "service", host_id: "demo", service_id: null } }),
        }),
      );
      renderPage();
      await screen.findByRole("heading", { level: 1 });
      expect(screen.queryByRole("button", { name: "I'm on it" })).toBeNull();
    });

    it("names the service in the menu", async () => {
      stubWithPut();
      renderPage();
      await userEvent.click(await screen.findByRole("button", { name: "I'm on it" }));
      expect(await screen.findByText("Snooze shop")).toBeInTheDocument();
    });

    it("a host incident's menu says Snooze with the host's name, or its id if unlisted", async () => {
      const host = detail({ incident: incident({ subject: subject("host") }) });
      stubBackend(host);
      const { unmount } = renderPage();
      await userEvent.click(await screen.findByRole("button", { name: "I'm on it" }));
      expect(await screen.findByText("Snooze Demo stack")).toBeInTheDocument();
      unmount();

      hostList = [];
      stubBackend(host);
      renderPage();
      await userEvent.click(await screen.findByRole("button", { name: "I'm on it" }));
      expect(await screen.findByText("Snooze demo")).toBeInTheDocument();
    });

    it("a host incident's I'm on it targets the host", async () => {
      const host = detail({ incident: incident({ subject: subject("host") }) });
      const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
        const overallReply = answerOverall(input);
        if (overallReply) return overallReply;
        if (init?.method === "PUT") return json({});
        if (String(input).includes("/events")) return json([]);
        return json(host);
      });
      vi.stubGlobal("fetch", fetchMock);
      renderPage();
      await userEvent.click(await screen.findByRole("button", { name: "I'm on it" }));
      await userEvent.click(await screen.findByRole("menuitem", { name: /Leave it alone/ }));
      await waitFor(() =>
        expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(true),
      );
      const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT")!;
      expect(String(put[0])).toBe("/api/quiet/hosts/demo");
    });
  });
});
