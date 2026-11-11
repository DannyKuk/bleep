import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { MemoryPanel } from "@/components/service/memory-panel";
import type { ServiceFact } from "@/lib/api-types";
import { setLanguage } from "@/lib/i18n";

const FACTS = "/api/services/demo%2Fcompose%3Ashop/facts";

const fact = (overrides: Partial<ServiceFact> = {}): ServiceFact => ({
  id: 1,
  text: "The db is shared with billing.",
  source: "human",
  created_at: "2026-11-02T18:00:00Z",
  incident_id: null,
  ...overrides,
});

function json(body: unknown, status = 200) {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** Answers each request with the next reply, in order. */
function stubBackend(...replies: Response[]) {
  const fetchMock = vi.fn<typeof fetch>(async () => replies.shift() ?? json([]));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("MemoryPanel", () => {
  it("lists a person's facts and the ones learned from a report, with where each came from", async () => {
    const fetchMock = stubBackend(
      json([
        fact(),
        fact({
          id: 2,
          text: "Always redeploy shop rather than restart it.",
          source: "report",
          incident_id: "inc-7",
        }),
      ]),
    );
    render(<MemoryPanel serviceId="demo/compose:shop" />);

    const list = await screen.findByRole("list", { name: "Facts" });
    expect(fetchMock.mock.calls[0][0]).toBe(FACTS);
    const [mine, learned] = within(list).getAllByRole("listitem");
    expect(mine).toHaveTextContent("The db is shared with billing.");
    expect(mine).toHaveTextContent("Added by you");
    expect(learned).toHaveTextContent("Learned from an incident's report");
    expect(within(learned).getByRole("link")).toHaveAttribute("href", "/incidents/inc-7");
  });

  it("adds a fact", async () => {
    const fetchMock = stubBackend(
      json([]),
      json(fact({ id: 3, text: "The worker is batch only." })),
    );
    render(<MemoryPanel serviceId="demo/compose:shop" />);
    expect(await screen.findByText(/No facts yet/)).toBeInTheDocument();

    await userEvent.type(screen.getByRole("textbox"), "  The worker is batch only. ");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));

    expect(fetchMock.mock.calls[1][0]).toBe(FACTS);
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: "POST" });
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({
      text: "The worker is batch only.",
    });
    expect(await screen.findByText("The worker is batch only.")).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue("");
  });

  it("deletes a fact", async () => {
    const fetchMock = stubBackend(json([fact()]), json(null, 204));
    render(<MemoryPanel serviceId="demo/compose:shop" />);

    await userEvent.click(
      await screen.findByRole("button", {
        name: "Delete the fact: The db is shared with billing.",
      }),
    );

    expect(fetchMock.mock.calls[1][0]).toBe(`${FACTS}/1`);
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: "DELETE" });
    await waitFor(() =>
      expect(screen.queryByText("The db is shared with billing.")).not.toBeInTheDocument(),
    );
    expect(screen.getByText(/No facts yet/)).toBeInTheDocument();
  });

  it("keeps the fact and says so when the delete fails", async () => {
    stubBackend(json([fact()]), json({ detail: "no such fact" }, 404));
    render(<MemoryPanel serviceId="demo/compose:shop" />);

    await userEvent.click(await screen.findByRole("button", { name: /Delete the fact/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not delete the fact: no such fact",
    );
    expect(screen.getByText("The db is shared with billing.")).toBeInTheDocument();
  });

  it("speaks German, with German dates, and keeps a fact's text as written", async () => {
    act(() => setLanguage("de"));
    stubBackend(
      json([
        fact(),
        fact({ id: 2, text: "Always redeploy.", source: "report", incident_id: "inc-7" }),
      ]),
    );
    render(<MemoryPanel serviceId="demo/compose:shop" />);

    const list = await screen.findByRole("list", { name: "Fakten" });
    const [mine, learned] = within(list).getAllByRole("listitem");
    expect(mine).toHaveTextContent("The db is shared with billing.");
    expect(mine).toHaveTextContent(/Von dir hinzugefügt, 02\.11\.2026, \d\d:\d\d/);
    expect(learned).toHaveTextContent(/Gelernt aus dem Bericht eines Vorfalls, 02\.11\.2026/);
    expect(
      within(mine).getByRole("button", { name: "Fakt löschen: The db is shared with billing." }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Bleep liest diese bei jedem Vorfall dieses Dienstes."),
    ).toBeInTheDocument();
    expect(
      screen.getByLabelText("Etwas, das Bleep über diesen Dienst immer wissen soll"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hinzufügen" })).toBeInTheDocument();
  });

  it("says in German that the facts could not be read", async () => {
    act(() => setLanguage("de"));
    stubBackend(json({ detail: "boom" }, 500));
    render(<MemoryPanel serviceId="demo/compose:shop" />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Die Fakten konnten nicht geladen werden",
    );
  });
});
