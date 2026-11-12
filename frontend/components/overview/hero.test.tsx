import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ChatOpenerProvider } from "@/components/chat/chat-opener";
import { ChatPanel } from "@/components/chat/chat-panel";
import { json } from "@/components/dev/fixtures";
import { OverallProvider } from "@/components/overall-state";
import { host, service } from "@/components/overview/fixtures";
import { OverviewHero } from "@/components/overview/hero";
import type { ServiceView } from "@/lib/api-types";

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));

const SHOP = service({
  health: "down",
  containers: [{ ...service().containers[0], state: "exited", exit_code: 1 }],
  unwatched: {
    reason: "before_start",
    container: "shop-web-1",
    incident_id: null,
    incident_short_name: null,
  },
});

function stubReads() {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const path = String(input);
    if (path.startsWith("/api/incidents")) return json([]);
    if (path.startsWith("/api/hosts")) return json([host()]);
    return json([]);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function Hero({ services }: { services: ServiceView[] }) {
  return (
    <OverviewHero
      situation={{ kind: "unwatched", services }}
      mood="pointing"
      hosts={[host()]}
      services={services}
    />
  );
}

describe("Ask Bleep to look (#478)", () => {
  it("opens the chat panel with the look typed in, for the person to send", async () => {
    const fetchMock = stubReads();
    render(
      <OverallProvider>
        <ChatOpenerProvider>
          <Hero services={[SHOP]} />
          <ChatPanel />
        </ChatOpenerProvider>
      </OverallProvider>,
    );
    expect(screen.queryByRole("region", { name: "Chat with Bleep" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Ask Bleep to look" }));
    expect(screen.getByRole("region", { name: "Chat with Bleep" })).toBeInTheDocument();
    const input = screen.getByRole("textbox", { name: "Message to Bleep" });
    expect(input).toHaveValue("Look at shop");
    expect(input).toHaveFocus();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("types the look over a draft when the panel is already open", async () => {
    stubReads();
    const blog = service({ id: "demo/compose:blog", name: "blog", health: "down" });
    render(
      <OverallProvider>
        <ChatOpenerProvider>
          <Hero services={[blog, SHOP]} />
          <ChatPanel />
        </ChatOpenerProvider>
      </OverallProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Talk to Bleep" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Message to Bleep" }), "hello");
    await userEvent.click(screen.getByRole("button", { name: "Ask Bleep to look" }));
    expect(screen.getByRole("textbox", { name: "Message to Bleep" })).toHaveValue("Look at blog");
  });

  it("does nothing without the panel, as on a page of its own", async () => {
    render(<Hero services={[SHOP]} />);
    await userEvent.click(screen.getByRole("button", { name: "Ask Bleep to look" }));
    expect(screen.queryByRole("region", { name: "Chat with Bleep" })).toBeNull();
  });
});
