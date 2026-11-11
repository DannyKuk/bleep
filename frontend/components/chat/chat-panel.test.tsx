import { act, render, screen, waitFor, within } from "@testing-library/react";
import { useRef } from "react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChatPanel } from "@/components/chat/chat-panel";
import { json } from "@/components/dev/fixtures";
import { incident } from "@/components/incident/fixtures";
import { OverallProvider } from "@/components/overall-state";
import { host } from "@/components/overview/fixtures";
import { PinnedBarProvider, useReportPinnedBar } from "@/components/pinned-bar";
import type { Incident } from "@/lib/api-types";
import { setLanguage } from "@/lib/i18n";
import type { ChatAnswer, LookProgress, TalkLink } from "@/lib/talk-api";

let pathname = "/";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));

const SHOP = "demo/compose:shop";
const SHOP_14 = incident({ id: "inc_14", short_name: "shop #14" });

type Answerer = (path: string, body: unknown) => Response | Promise<Response>;

/** The overall reads answer `incidents`; the chat's posts go to `talk`. */
function stubBackend(talk: Answerer, incidents: Incident[] = [SHOP_14]) {
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const path = String(input);
    if (path.startsWith("/api/incidents")) return json(incidents);
    if (path.startsWith("/api/hosts")) return json([host()]);
    return talk(path, init?.body ? JSON.parse(String(init.body)) : null);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function posts(fetchMock: ReturnType<typeof stubBackend>) {
  return fetchMock.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([input, init]) => [String(input), JSON.parse(String(init!.body))]);
}

function renderPanel() {
  const view = render(
    <OverallProvider>
      <ChatPanel />
    </OverallProvider>,
  );
  const rerender = () =>
    view.rerender(
      <OverallProvider>
        <ChatPanel />
      </OverallProvider>,
    );
  return { rerender };
}

function answer(overrides: Partial<ChatAnswer> = {}): ChatAnswer {
  return { answer: "Everything is fine.", links: [], confirm: null, ...overrides };
}

const SNOOZE = answer({
  answer: "🤫 Snooze shop until 16:04?",
  links: [{ kind: "service", id: SHOP, label: "shop" }],
  confirm: {
    id: "c1",
    yes_label: "Yes",
    no_label: "No",
    expires_at: "2999-01-01T00:00:00Z",
    incident_id: null,
  },
});

async function write(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.type(screen.getByRole("textbox", { name: "Message to Bleep" }), text);
  await user.click(screen.getByRole("button", { name: "Send" }));
}

describe("ChatPanel", () => {
  beforeEach(() => {
    pathname = "/";
  });

  it("opens from Bleep's face and closes again", async () => {
    stubBackend(() => json(answer()));
    const user = userEvent.setup();
    renderPanel();

    await user.click(screen.getByRole("button", { name: "Talk to Bleep" }));
    const panel = screen.getByRole("region", { name: "Chat with Bleep" });
    expect(within(panel).getByRole("heading", { name: "Bleep" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Message to Bleep" })).toHaveFocus();

    await user.click(screen.getByRole("button", { name: "Close chat" }));
    expect(screen.queryByRole("region", { name: "Chat with Bleep" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Talk to Bleep" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("region", { name: "Chat with Bleep" })).not.toBeInTheDocument();
  });

  it("exports the chat as the backend's Markdown transcript from its header, once at a time", async () => {
    let release = () => {};
    const held = new Promise<void>((resolve) => (release = resolve));
    const fetchMock = stubBackend(async () => {
      await held;
      return new Response("# Chat with Bleep\n", {
        headers: {
          "content-type": "text/markdown",
          "content-disposition": 'attachment; filename="bleep-chat-20261110-1402.md"',
        },
      });
    });
    // jsdom has no object URLs; a browser turns the fetched file into one to save it.
    const objectUrls = {
      createObjectURL: URL.createObjectURL,
      revokeObjectURL: URL.revokeObjectURL,
    };
    Object.assign(URL, { createObjectURL: vi.fn(() => "blob:chat"), revokeObjectURL: vi.fn() });
    const saved: { href: string; download: string }[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      saved.push({ href: this.href, download: this.download });
    });
    try {
      const user = userEvent.setup();
      renderPanel();
      await user.click(screen.getByRole("button", { name: "Talk to Bleep" }));

      const panel = screen.getByRole("region", { name: "Chat with Bleep" });
      const exportButton = within(within(panel).getByRole("banner")).getByRole("button", {
        name: "Export chat",
      });
      await user.click(exportButton);
      expect(exportButton).toBeDisabled(); // a second click while it fetches starts nothing
      await user.click(exportButton);
      release();

      await waitFor(() =>
        expect(saved).toEqual([{ href: "blob:chat", download: "bleep-chat-20261110-1402.md" }]),
      );
      const logFetches = fetchMock.mock.calls.filter(([input]) => String(input).includes("/log"));
      expect(logFetches).toEqual([["/api/talk/log?format=md", { credentials: "same-origin" }]]);
      await waitFor(() => expect(exportButton).toBeEnabled());
    } finally {
      click.mockRestore();
      Object.assign(URL, objectUrls);
    }
  });

  it("says so when the session ended instead of saving the error as the chat", async () => {
    stubBackend(() => json({ detail: "not authenticated" }, 401));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click");
    const user = userEvent.setup();
    renderPanel();
    await user.click(screen.getByRole("button", { name: "Talk to Bleep" }));

    await user.click(screen.getByRole("button", { name: "Export chat" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Your session has ended. Log in again to export the chat.",
    );
    expect(click).not.toHaveBeenCalled();
    click.mockRestore();
  });

  it("shows a status answer with in-app links built from the ids the backend sent", async () => {
    const fetchMock = stubBackend(() =>
      json(
        answer({
          answer: "shop is down since 14:02; shop #14 waits for your yes.",
          links: [
            { kind: "service", id: SHOP, label: "shop" },
            { kind: "incident", id: "inc_14", label: "shop #14" },
          ],
        }),
      ),
    );
    const user = userEvent.setup();
    renderPanel();
    await user.click(screen.getByRole("button", { name: "Talk to Bleep" }));

    await write(user, "how do things look?");

    expect(await screen.findByRole("link", { name: "shop" })).toHaveAttribute(
      "href",
      "/services/demo%2Fcompose%3Ashop",
    );
    expect(screen.getByRole("link", { name: "shop #14" })).toHaveAttribute(
      "href",
      "/incidents/inc_14",
    );
    expect(screen.getByText("how do things look?")).toBeInTheDocument();
    expect(posts(fetchMock)).toEqual([
      ["/api/talk/messages", { text: "how do things look?", incident_id: null }],
    ]);
  });

  it("runs an action only on the press of its button, which then turns into a line", async () => {
    const fetchMock = stubBackend((path) =>
      path === "/api/talk/messages"
        ? json(SNOOZE)
        : json({ line: "Snoozed until 16:04. Bleep leaves it alone until then." }),
    );
    const user = userEvent.setup();
    renderPanel();
    await user.click(screen.getByRole("button", { name: "Talk to Bleep" }));

    await write(user, "leave shop alone for 2 hours");
    await screen.findByRole("button", { name: "Yes" });
    expect(screen.getByRole("button", { name: "No" })).toBeInTheDocument();
    expect(posts(fetchMock)).toHaveLength(1); // the message alone asked for nothing to run

    await user.click(screen.getByRole("button", { name: "Close chat" }));
    expect(screen.getByTestId("chat-dot")).toBeInTheDocument(); // an open question waits
    await user.click(screen.getByRole("button", { name: /Talk to Bleep/ }));

    await user.click(screen.getByRole("button", { name: "Yes" }));

    expect(
      await screen.findByText("Snoozed until 16:04. Bleep leaves it alone until then."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Yes" })).not.toBeInTheDocument();
    expect(posts(fetchMock)[1]).toEqual(["/api/talk/confirms/c1", { yes: true }]);
  });

  it("says when a message could not be answered and keeps the words to send again", async () => {
    stubBackend(() => json({ detail: "the model is away" }, 503));
    const user = userEvent.setup();
    renderPanel();
    await user.click(screen.getByRole("button", { name: "Talk to Bleep" }));

    await write(user, "how is shop?");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Bleep could not answer: the model is away.",
    );
    expect(screen.getByRole("textbox", { name: "Message to Bleep" })).toHaveValue("how is shop?");
  });

  it("keeps the buttons when a press fails, and says so", async () => {
    stubBackend((path) =>
      path === "/api/talk/messages" ? json(SNOOZE) : Promise.reject(new TypeError("offline")),
    );
    const user = userEvent.setup();
    renderPanel();
    await user.click(screen.getByRole("button", { name: "Talk to Bleep" }));
    await write(user, "leave shop alone for 2 hours");

    await user.click(await screen.findByRole("button", { name: "Yes" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Bleep could not be reached.");
    expect(screen.getByRole("button", { name: "Yes" })).toBeEnabled();
  });

  it("shows an offer past its time, or about an incident that ended, as expired", async () => {
    const ended = incident({ id: "inc_9", short_name: "shop #9", status: "resolved" });
    const replies = [
      answer({ confirm: { ...SNOOZE.confirm!, id: "old", expires_at: "2000-01-01T00:00:00Z" } }),
      answer({ confirm: { ...SNOOZE.confirm!, id: "c9", incident_id: "inc_9" } }),
    ];
    stubBackend(() => json(replies.shift()), [ended]);
    const user = userEvent.setup();
    renderPanel();
    await user.click(screen.getByRole("button", { name: "Talk to Bleep" }));

    await write(user, "snooze shop");
    await write(user, "that's expected");

    await waitFor(() => expect(screen.getAllByText("Expired")).toHaveLength(2));
    expect(screen.queryByRole("button", { name: "Yes" })).not.toBeInTheDocument();
  });

  it("on an incident page sends that incident along until its chip is removed", async () => {
    pathname = "/incidents/inc_14";
    const fetchMock = stubBackend(() => json(answer()));
    const user = userEvent.setup();
    renderPanel();
    await user.click(screen.getByRole("button", { name: "Talk to Bleep" }));

    expect(await screen.findByText("shop #14")).toBeInTheDocument();
    await write(user, "why did you restart it?");
    await user.click(screen.getByRole("button", { name: "Leave out shop #14" }));
    await write(user, "and now?");

    await waitFor(() => expect(posts(fetchMock)).toHaveLength(2));
    expect(posts(fetchMock).map(([, body]) => body.incident_id)).toEqual(["inc_14", null]);
  });

  it("on a page whose path is no incident id shows no chip and sends no incident", async () => {
    pathname = `/incidents/${"x".repeat(65)}`;
    const fetchMock = stubBackend(() => json(answer()));
    const user = userEvent.setup();
    renderPanel();
    await user.click(screen.getByRole("button", { name: "Talk to Bleep" }));

    await write(user, "how is it?");

    await waitFor(() => expect(posts(fetchMock)).toHaveLength(1));
    expect(posts(fetchMock)[0][1].incident_id).toBeNull();
    expect(screen.queryByText(/^About/)).not.toBeInTheDocument();
  });

  it("says a message is too long only when the backend refused its words", async () => {
    const invalid = (field: string) =>
      json({ detail: [{ loc: ["body", field], msg: "too long", type: "string_too_long" }] }, 422);
    const replies = [invalid("text"), invalid("incident_id")];
    stubBackend(() => replies.shift()!);
    const user = userEvent.setup();
    renderPanel();
    await user.click(screen.getByRole("button", { name: "Talk to Bleep" }));

    await write(user, "first");
    expect(await screen.findByText("That message is too long.")).toBeInTheDocument();
    await user.clear(screen.getByRole("textbox", { name: "Message to Bleep" }));
    await write(user, "second");

    expect(await screen.findByText("Bleep could not take that message.")).toBeInTheDocument();
    expect(screen.getAllByText("That message is too long.")).toHaveLength(1);
  });

  it("stays open with its conversation across page changes", async () => {
    stubBackend(() => json(answer({ answer: "All good." })));
    const user = userEvent.setup();
    const { rerender } = renderPanel();
    await user.click(screen.getByRole("button", { name: "Talk to Bleep" }));
    await write(user, "how is it?");
    await screen.findByText("All good.");

    pathname = "/hosts";
    act(rerender);

    expect(screen.getByRole("region", { name: "Chat with Bleep" })).toBeInTheDocument();
    expect(screen.getByText("All good.")).toBeInTheDocument();
  });

  describe("following a look it started (#458)", () => {
    const LOOK = answer({
      answer: "🔎 Look at shop now? It costs a model run.",
      confirm: { ...SNOOZE.confirm!, id: "look1" },
    });
    const PRESSED = "🔎 Looking at shop now as shop #7. I'll tell you here what I find.";
    const SHOP_7: TalkLink = { kind: "incident", id: "inc_7", label: "shop #7" };

    function look(overrides: Partial<LookProgress>): LookProgress {
      return { state: "running", line: "Looking into it…", links: [], steps: [], ...overrides };
    }

    /** The look's answers in turn, the last one again for every ask after them. */
    function stubLook(...answers: (LookProgress | Error)[]) {
      const asked: string[] = [];
      const fetchMock = stubBackend((path) => {
        if (path === "/api/talk/messages") return json(LOOK);
        if (path.startsWith("/api/talk/confirms/")) {
          return json({ line: PRESSED, incident_id: "inc_7" });
        }
        asked.push(path);
        const next = answers.length > 1 ? answers.shift()! : answers[0];
        return next instanceof Error ? Promise.reject(next) : json(next);
      });
      return { asked, fetchMock };
    }

    async function pressLook() {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      renderPanel();
      await user.click(screen.getByRole("button", { name: "Talk to Bleep" }));
      await write(user, "can you check up shop-web?");
      await user.click(await screen.findByRole("button", { name: "Yes" }));
      await screen.findByText(PRESSED);
      return user;
    }

    const wait = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));

    afterEach(() => {
      vi.useRealTimers();
    });

    it("shows the run's progress, then its summary with links and what it did, then stops", async () => {
      const summary = "✅ shop #7: shop is healthy (no URL checked).\nAll containers ran.";
      const steps = ["Listed the containers", "Read the logs of shop-web, shop-db"];
      const { asked } = stubLook(
        look({}),
        look({ line: "Reading through the container logs…", steps: steps.slice(0, 1) }),
        look({ state: "ended", line: summary, links: [SHOP_7], steps }),
      );
      await pressLook();

      expect(await screen.findByText("Looking into it…")).toBeInTheDocument();
      expect(asked).toEqual(["/api/talk/looks/inc_7"]);
      await wait(2_000);
      expect(screen.getByText("Reading through the container logs…")).toBeInTheDocument();
      expect(screen.getByRole("list", { name: "What I did" })).toHaveTextContent(
        "Listed the containers",
      );

      await wait(2_000);
      const followed = screen.getByTestId("look");
      expect(followed).toHaveTextContent("shop is healthy (no URL checked).");
      expect(within(followed).getByRole("link", { name: "shop #7" })).toHaveAttribute(
        "href",
        "/incidents/inc_7",
      );
      expect(
        within(screen.getByRole("list", { name: "What I did" }))
          .getAllByRole("listitem")
          .map((item) => item.textContent),
      ).toEqual(steps);
      expect(screen.queryByText("Reading through the container logs…")).not.toBeInTheDocument();

      await wait(30_000);
      expect(asked).toHaveLength(3); // ended: no more asks
    });

    it("keeps following, slower, while the plan waits, and through the fix to its end", async () => {
      const waits = "🙋 shop #7 found a problem: shop-web was OOM-killed.\nIts plan waits.";
      const fixed = "✅ shop #7: fixed in 4 min\nshop-web runs again.";
      const { asked } = stubLook(
        look({ state: "waiting", line: waits, links: [SHOP_7], steps: ["Listed the containers"] }),
        look({ line: "Running the plan…", steps: ["Listed the containers"] }),
        look({ line: "Checking it's healthy again…", steps: ["Restarted shop-web"] }),
        look({ state: "ended", line: fixed, links: [SHOP_7], steps: ["Restarted shop-web"] }),
      );
      await pressLook();

      expect(await screen.findByText(/found a problem/)).toBeInTheDocument();
      await wait(2_000);
      expect(asked).toHaveLength(1); // a waiting plan is asked about every 10 s
      await wait(8_000);
      expect(asked).toHaveLength(2);
      expect(screen.getByText("Running the plan…")).toBeInTheDocument();

      await wait(2_000);
      expect(screen.getByText("Checking it's healthy again…")).toBeInTheDocument();
      await wait(2_000);
      expect(screen.getByTestId("look")).toHaveTextContent("fixed in 4 min");
      expect(screen.getByRole("list", { name: "What I did" })).toHaveTextContent(
        "Restarted shop-web",
      );
      await wait(20_000);
      expect(asked).toHaveLength(4);
    });

    it("ends a rejected plan's look with the backend's line", async () => {
      const waits = "🙋 shop #7 found a problem: shop-web exited.\nIts plan waits.";
      const rejected = "🙅 shop #7: left to you, nothing was changed.\nNothing ran.";
      const { asked } = stubLook(
        look({ state: "waiting", line: waits, links: [SHOP_7] }),
        look({ state: "ended", line: rejected, links: [SHOP_7] }),
      );
      await pressLook();

      await screen.findByText(/found a problem/);
      await wait(10_000);
      expect(screen.getByTestId("look")).toHaveTextContent("left to you, nothing was changed.");
      await wait(20_000);
      expect(asked).toHaveLength(2);
    });

    it("keeps the last line when an ask fails, says so, and asks again", async () => {
      stubLook(
        look({ line: "Searching the docs…" }),
        new TypeError("offline"),
        look({ line: "Writing it up…" }),
      );
      await pressLook();

      expect(await screen.findByText("Searching the docs…")).toBeInTheDocument();
      await wait(2_000);
      expect(screen.getByText("Searching the docs…")).toBeInTheDocument();
      expect(within(screen.getByTestId("look")).getByRole("alert")).toHaveTextContent(
        "Lost track of the look for a moment.",
      );

      await wait(2_000);
      expect(screen.getByText("Writing it up…")).toBeInTheDocument();
      expect(within(screen.getByTestId("look")).queryByRole("alert")).not.toBeInTheDocument();
    });

    it("scrolls the chat down only when an answer shows something new", async () => {
      const waits = "🙋 shop #7 found a problem: shop-web exited.\nIts plan waits.";
      const waiting = look({ state: "waiting", line: waits, links: [SHOP_7] });
      const { asked } = stubLook(
        waiting,
        { ...waiting, links: [{ ...SHOP_7 }] },
        waiting,
        look({ line: "Running the plan…" }),
      );
      await pressLook();
      await screen.findByText(/found a problem/);
      const chat = screen.getByRole("region", { name: "Chat with Bleep" }).querySelector("ol")!;
      const scrolled = vi.fn();
      Object.defineProperty(chat, "scrollTop", { get: () => 0, set: scrolled, configurable: true });

      await wait(20_000);
      expect(asked).toHaveLength(3);
      expect(scrolled).not.toHaveBeenCalled(); // the same plan, asked about twice more

      await wait(10_000);
      expect(screen.getByText("Running the plan…")).toBeInTheDocument();
      expect(scrolled).toHaveBeenCalled();
    });

    it("stops asking once the panel is closed", async () => {
      const { asked } = stubLook(look({}));
      const user = await pressLook();
      await screen.findByText("Looking into it…");

      await user.click(screen.getByRole("button", { name: "Close chat" }));
      await wait(10_000);

      expect(asked).toHaveLength(1);
    });

    it("follows nothing after a press that is no look", async () => {
      stubBackend((path) =>
        path === "/api/talk/messages"
          ? json(SNOOZE)
          : json({ line: "Snoozed until 16:04.", incident_id: null }),
      );
      const user = userEvent.setup();
      renderPanel();
      await user.click(screen.getByRole("button", { name: "Talk to Bleep" }));
      await write(user, "leave shop alone");
      await user.click(await screen.findByRole("button", { name: "Yes" }));

      await screen.findByText("Snoozed until 16:04.");
      expect(screen.queryByTestId("look")).not.toBeInTheDocument();
    });
  });

  it("sits above a bar pinned to the screen's bottom, and goes back down once it is gone", () => {
    stubBackend(() => json(answer()));
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      top: window.innerHeight - 200,
    } as DOMRect);
    function Bar() {
      const ref = useRef<HTMLDivElement>(null);
      useReportPinnedBar(ref, true, 0);
      return <div ref={ref}>answer bar</div>;
    }
    const page = (bar: boolean) => (
      <OverallProvider>
        <PinnedBarProvider>
          {bar && <Bar />}
          <ChatPanel />
        </PinnedBarProvider>
      </OverallProvider>
    );
    const { rerender } = render(page(true));
    expect(screen.getByRole("button", { name: "Talk to Bleep" })).toHaveStyle({ bottom: "212px" });

    rerender(page(false));
    expect(screen.getByRole("button", { name: "Talk to Bleep" }).style.bottom).toBe("");
  });
});

describe("ChatPanel in German", () => {
  beforeEach(() => {
    pathname = "/";
    act(() => setLanguage("de"));
  });

  it("names its own controls and failures in German", async () => {
    stubBackend(() => json({ detail: "das Modell ist weg" }, 503));
    const user = userEvent.setup();
    renderPanel();

    await user.click(screen.getByRole("button", { name: "Mit Bleep sprechen" }));
    const panel = screen.getByRole("region", { name: "Chat mit Bleep" });
    expect(within(panel).getByText("Fragt, bevor es etwas ändert")).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Chat exportieren" })).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Chat schließen" })).toBeInTheDocument();
    expect(within(panel).getByText(/Frag, wie es aussieht/)).toBeInTheDocument();

    await user.type(
      screen.getByRole("textbox", { name: "Nachricht an Bleep" }),
      "wie geht es shop?",
    );
    await user.click(screen.getByRole("button", { name: "Senden" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Bleep konnte nicht antworten: das Modell ist weg.",
    );
  });

  it("asks about the incident of the page and lets its chip go, in German", async () => {
    pathname = "/incidents/inc_14";
    stubBackend(() => json(answer()));
    const user = userEvent.setup();
    renderPanel();
    await user.click(screen.getByRole("button", { name: "Mit Bleep sprechen" }));

    expect(await screen.findByText("shop #14")).toBeInTheDocument();
    expect(screen.getByText(/^Zu/)).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Frag etwas zu diesem Vorfall…")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "shop #14 weglassen" }));
    expect(
      screen.getByPlaceholderText("Frag Bleep oder sag ihm, was es tun soll…"),
    ).toBeInTheDocument();
  });

  it("shows an offer past its time as expired", async () => {
    stubBackend(() =>
      json(
        answer({ confirm: { ...SNOOZE.confirm!, id: "old", expires_at: "2000-01-01T00:00:00Z" } }),
      ),
    );
    const user = userEvent.setup();
    renderPanel();
    await user.click(screen.getByRole("button", { name: "Mit Bleep sprechen" }));
    await user.type(screen.getByRole("textbox", { name: "Nachricht an Bleep" }), "stumm");
    await user.click(screen.getByRole("button", { name: "Senden" }));
    expect(await screen.findByText("Abgelaufen")).toBeInTheDocument();
  });

  it("keeps the words the backend wrote for a look as they came", async () => {
    const asked = answer({
      answer: "🔎 Jetzt shop anschauen?",
      confirm: { ...SNOOZE.confirm!, id: "look1" },
    });
    stubBackend((path) => {
      if (path === "/api/talk/messages") return json(asked);
      if (path.startsWith("/api/talk/confirms/")) {
        return json({ line: "🔎 Ich schaue mir shop jetzt an.", incident_id: "inc_7" });
      }
      return json({
        state: "ended",
        line: "✅ Angeschaut: shop ist gesund",
        links: [],
        steps: ["Container aufgelistet"],
      });
    });
    const user = userEvent.setup();
    renderPanel();
    await user.click(screen.getByRole("button", { name: "Mit Bleep sprechen" }));
    await user.type(
      screen.getByRole("textbox", { name: "Nachricht an Bleep" }),
      "schau dir shop an",
    );
    await user.click(screen.getByRole("button", { name: "Senden" }));
    await user.click(await screen.findByRole("button", { name: "Yes" }));

    expect(await screen.findByText("✅ Angeschaut: shop ist gesund")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Was ich getan habe" })).toHaveTextContent(
      "Container aufgelistet",
    );
  });
});
