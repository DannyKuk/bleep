import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ChatPanel } from "@/components/chat/chat-panel";
import { json } from "@/components/dev/fixtures";
import { incident } from "@/components/incident/fixtures";
import { OverallProvider } from "@/components/overall-state";
import { host } from "@/components/overview/fixtures";
import type { Incident } from "@/lib/api-types";
import type { ChatAnswer } from "@/lib/talk-api";

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
    await user.click(screen.getByRole("button", { name: "Do not send shop #14 with the message" }));
    await write(user, "and now?");

    await waitFor(() => expect(posts(fetchMock)).toHaveLength(2));
    expect(posts(fetchMock).map(([, body]) => body.incident_id)).toEqual(["inc_14", null]);
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
});
