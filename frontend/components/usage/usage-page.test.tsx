import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { json } from "@/components/dev/fixtures";
import { BUSY, EMPTY, forRange, LOCAL_ONLY } from "@/components/usage/fixtures";
import { UsageContent, UsagePage } from "@/components/usage/usage-page";
import type { UsageView } from "@/lib/api-types";
import { setLanguage } from "@/lib/i18n";
import { resetModelSlots } from "@/lib/model-colours";

beforeEach(() => resetModelSlots());
afterEach(() => resetModelSlots());

const tile = (label: string) => screen.getByRole("group", { name: label });
const chart = (name: string) => screen.getByRole("region", { name });
const bars = (name: string) =>
  within(chart(name)).getByRole("region", { name: `${name}, one bar per day` });
const row = (table: HTMLElement, text: string) =>
  within(table)
    .getAllByRole("row")
    .find((r) => r.textContent?.includes(text)) as HTMLElement;

describe("a busy range", () => {
  beforeEach(() => {
    render(<UsageContent usage={BUSY} />);
  });

  it("shows the five tiles", () => {
    expect(tile("Spent")).toHaveTextContent("$1.50");
    expect(tile("Spent")).toHaveTextContent("6 free local calls · + 3 without a known price");
    expect(tile("Model calls")).toHaveTextContent("2420 incidents · 4 chat");
    expect(tile("Tokens")).toHaveTextContent("1.2M in · 34K out");
    expect(tile("Read from cache")).toHaveTextContent("25%of the input tokens");
    expect(tile("Incidents")).toHaveTextContent("7$0.25 on average");
  });

  it("tells each model and its provider apart, free from unknown", () => {
    const table = screen.getByRole("table", { name: "Models" });
    const sonnet = row(table, "claude-sonnet-5-5");
    expect(sonnet).toHaveTextContent("Anthropic");
    expect(sonnet).toHaveTextContent("300K");
    expect(sonnet).toHaveTextContent("$1.20");
    expect(sonnet).toHaveTextContent("80%");
    const luna = row(table, "openai/gpt-6-luna");
    expect(luna).toHaveTextContent("OpenRouter");
    expect(within(luna).getAllByRole("cell")[5]).toHaveTextContent("–");
    expect(luna).toHaveTextContent("20%");
    const local = row(table, "qwen3:8b");
    expect(local).toHaveTextContent("Local");
    expect(within(local).getAllByRole("cell")[6]).toHaveTextContent("free");
    const old = row(table, "mystery/m");
    expect(within(old).getAllByRole("cell")[1]).toHaveTextContent("unknown");
    expect(within(old).getAllByRole("cell")[6]).toHaveTextContent("unknown");
  });

  it("draws a bar for each of the 30 days, ending today, with a 2 px gap between segments", () => {
    const spend = bars("Spend per day");
    const days = [...spend.querySelectorAll("[data-day]")].map((d) => d.getAttribute("data-day"));
    expect(days).toHaveLength(30);
    expect(days[0]).toBe("2026-10-15");
    expect(days[29]).toBe("2026-11-13");
    const stack = spend.querySelector('[data-day="2026-11-12"] [data-series]')?.parentElement;
    expect(stack?.className).toContain("gap-[2px]");
    expect(stack?.querySelectorAll("[data-series]")).toHaveLength(2);
  });

  it("colours a model by the model, not by its place in the list", () => {
    const spend = bars("Spend per day");
    const colourOf = (series: string) =>
      spend.querySelector(`[data-series="${series}"]`)?.getAttribute("style");
    expect(colourOf("model-1")).toContain("var(--viz-1)");
    expect(colourOf("model-2")).toContain("var(--viz-2)");
    const legend = within(chart("Spend per day")).getByRole("list", { name: "Legend" });
    expect(legend).toHaveTextContent("claude-sonnet-5-5");
    expect(legend).toHaveTextContent("openai/gpt-6-luna");
  });

  it("shows a day's numbers in a tooltip on hover", async () => {
    const user = userEvent.setup();
    const spend = bars("Spend per day");
    await user.hover(spend.querySelector('[data-day="2026-11-12"]') as HTMLElement);
    const tooltip = within(chart("Spend per day")).getByText("12 Nov").parentElement;
    expect(tooltip).toHaveTextContent("claude-sonnet-5-5$0.50");
    expect(tooltip).toHaveTextContent("openai/gpt-6-luna$0.30");
    expect(tooltip).toHaveTextContent("Total$0.80");
    await user.unhover(spend.querySelector('[data-day="2026-11-12"]') as HTMLElement);
    expect(within(chart("Spend per day")).queryByText("12 Nov")).not.toBeInTheDocument();
  });

  it("offers the same numbers as a table", async () => {
    const user = userEvent.setup();
    await user.click(within(chart("Model calls per day")).getByRole("button", { name: "Table" }));
    const table = within(chart("Model calls per day")).getByRole("table", {
      name: "Model calls per day",
    });
    const cells = within(row(table, "2026-11-12")).getAllByRole("cell");
    expect(cells.map((cell) => cell.textContent)).toEqual(["2026-11-12", "8", "3", "11"]);
    expect(within(table).queryByText("2026-11-01")).not.toBeInTheDocument();
  });

  it("splits the model calls per day into incidents and chat", () => {
    const calls = bars("Model calls per day");
    expect(calls.querySelectorAll('[data-day="2026-11-12"] [data-series]')).toHaveLength(2);
    expect(calls.querySelectorAll('[data-day="2026-11-13"] [data-series]')).toHaveLength(2);
    expect(
      within(chart("Model calls per day")).getByRole("list", { name: "Legend" }),
    ).toHaveTextContent("IncidentsChat");
  });

  it("keeps the calls chart's colours apart from every model's", () => {
    const calls = bars("Model calls per day");
    const colours = ["incidents", "chat"].map((key) =>
      calls.querySelector(`[data-series="${key}"]`)?.getAttribute("style"),
    );
    expect(colours.join(" ")).toContain("var(--viz-incidents)");
    expect(colours.join(" ")).toContain("var(--viz-chat)");
    expect(colours.join(" ")).not.toMatch(/var\(--viz-\d\)|var\(--accent\)/);
  });

  it("shows today against both caps and links the most expensive incident", () => {
    const card = screen.getByRole("region", { name: "Today against the caps" });
    expect(within(card).getByText("$0.85 of $1.00")).toBeInTheDocument();
    expect(within(card).getByText("$0.20 of $0.20")).toBeInTheDocument();
    expect(within(card).getByRole("meter", { name: "Incidents and recoveries" })).toHaveAttribute(
      "aria-valuenow",
      "0.85",
    );
    expect(within(card).getAllByText("Cap reached")).toHaveLength(1);
    expect(within(card).getByRole("link", { name: "shop-web died on a/shop" })).toHaveAttribute(
      "href",
      "/incidents/inc_shop",
    );
    expect(card).toHaveTextContent("$0.80");
  });

  it("counts how incidents ended, what was changed and what the guard stopped", () => {
    const endings = screen.getByRole("region", { name: "How incidents ended" });
    expect(endings).toHaveTextContent("Fixed and verified3");
    expect(endings).toHaveTextContent("Back on its own1");
    expect(endings).toHaveTextContent("Failed run1");
    expect(endings).not.toHaveTextContent("Still open");

    const actions = screen.getByRole("region", { name: "What Bleep changed after a yes" });
    expect(actions).toHaveTextContent("Restart a containerdone 3 · failed 1");
    expect(actions).toHaveTextContent("Redeploy on Coolifyskipped 1");
    expect(actions).toHaveTextContent("plugin.new_thingdone 2");

    const guard = screen.getByRole("region", { name: "What the guard stopped" });
    expect(guard).toHaveTextContent("Secrets redacted12");
    expect(guard).toHaveTextContent("Commands refused2");
    expect(guard).toHaveTextContent("future_kind1");
  });
});

describe("a daily cap of zero", () => {
  it("reads as reached, as the backend stops on spent >= cap", () => {
    const today = { spent_usd: 0, daily_cap_usd: 0, chat_spent_usd: 0, chat_daily_cap_usd: 0.2 };
    render(<UsageContent usage={{ ...BUSY, today }} />);
    const card = screen.getByRole("region", { name: "Today against the caps" });
    expect(within(card).getByText("$0.00 of $0.00")).toBeInTheDocument();
    expect(within(card).getAllByText("Cap reached")).toHaveLength(1);
    expect(within(card).queryByRole("meter", { name: "Incidents and recoveries" })).toBeNull();
    expect(within(card).getByRole("meter", { name: "Chat answers" })).toBeInTheDocument();
  });
});

describe("an empty range", () => {
  it("is one quiet line, with no table or chart", () => {
    render(<UsageContent usage={EMPTY} />);
    expect(screen.getByRole("status")).toHaveTextContent(
      "Bleep made no model calls and handled no incident in this range.",
    );
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Spend per day" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Today against the caps" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "How incidents ended" })).not.toBeInTheDocument();
  });
});

describe("a range with only local calls", () => {
  beforeEach(() => {
    render(<UsageContent usage={LOCAL_ONLY} />);
  });

  it("shows nothing spent, the calls as free and no division by nothing", () => {
    expect(tile("Spent")).toHaveTextContent("$0.008 free local calls");
    expect(tile("Read from cache")).toHaveTextContent("0%");
    expect(tile("Incidents")).toHaveTextContent("2");
    expect(tile("Incidents")).not.toHaveTextContent("on average");
    const local = row(screen.getByRole("table", { name: "Models" }), "qwen3:8b");
    expect(within(local).getAllByRole("cell")[6]).toHaveTextContent("free");
    expect(document.body.textContent).not.toMatch(/NaN|Infinity/);
  });

  it("says no spend chart is needed and still charts the calls", () => {
    expect(screen.getByText(/Nothing was spent in this range/)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Spend per day, one bar per day" })).toBeNull();
    expect(bars("Model calls per day").querySelectorAll("[data-day]")).toHaveLength(7);
  });

  it("counts an incident that is still open", () => {
    expect(screen.getByRole("region", { name: "How incidents ended" })).toHaveTextContent(
      "Still open1",
    );
  });
});

describe("a range with an incident but no model call", () => {
  it("shows the tiles and cards and says there were no calls", () => {
    const usage: UsageView = {
      ...EMPTY,
      incidents: { count: 1, average_cost_usd: null, most_expensive: null },
      endings: { ...EMPTY.endings, outcomes: { ...EMPTY.endings.outcomes, healthy: 1 } },
    };
    render(<UsageContent usage={usage} />);
    expect(screen.getByText("No model calls in this range.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "How incidents ended" })).toHaveTextContent(
      "Healthy when checked1",
    );
  });
});

function stubUsage(answer: (days: string) => Response | Promise<Response>) {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = new URL(String(input), "http://x");
    return answer(url.searchParams.get("days") ?? "");
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("the Usage page", () => {
  it("reads the last 30 days first and the range picked after", async () => {
    const user = userEvent.setup();
    const fetchMock = stubUsage((days) => json(forRange(BUSY, Number(days) as UsageView["days"])));
    render(<UsagePage />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading the usage");
    expect(await screen.findByRole("table", { name: "Models" })).toBeInTheDocument();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/usage?days=30");
    expect(screen.getByRole("button", { name: "30 days" })).toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByRole("button", { name: "7 days" }));
    await waitFor(() =>
      expect(bars("Spend per day").querySelectorAll("[data-day]")).toHaveLength(7),
    );
    expect(fetchMock.mock.calls.at(-1)?.[0]).toBe("/api/usage?days=7");
    await user.click(screen.getByRole("button", { name: "Today" }));
    await waitFor(() =>
      expect(bars("Spend per day").querySelectorAll("[data-day]")).toHaveLength(1),
    );
    await user.click(screen.getByRole("button", { name: "90 days" }));
    await waitFor(() =>
      expect(bars("Spend per day").querySelectorAll("[data-day]")).toHaveLength(90),
    );
  });

  it("does not show one range's numbers under another while it loads", async () => {
    const user = userEvent.setup();
    stubUsage((days) => (days === "30" ? json(BUSY) : new Promise<Response>(() => undefined)));
    render(<UsagePage />);
    await screen.findByRole("table", { name: "Models" });
    await user.click(screen.getByRole("button", { name: "90 days" }));
    expect(screen.getByRole("status")).toHaveTextContent("Loading the usage");
    expect(screen.queryByRole("table", { name: "Models" })).not.toBeInTheDocument();
  });

  it("does not carry a failed range's error over to the range picked next", async () => {
    const user = userEvent.setup();
    stubUsage((days) =>
      days === "30" ? json({ detail: "boom" }, 500) : new Promise<Response>(() => undefined),
    );
    render(<UsagePage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load the usage");
    await user.click(screen.getByRole("button", { name: "7 days" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading the usage");
  });

  it("charts the server's today even when the browser's date is another", async () => {
    const today = { ...forRange(BUSY, 1), through: "2026-11-13" };
    stubUsage(() => json(today));
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 10, 14, 0, 30));
    try {
      render(<UsagePage />);
      const spend = await screen.findByRole("region", {
        name: "Model calls per day, one bar per day",
      });
      expect(
        [...spend.querySelectorAll("[data-day]")].map((d) => d.getAttribute("data-day")),
      ).toEqual(["2026-11-13"]);
      expect(
        spend.querySelectorAll('[data-day="2026-11-13"] [data-series]').length,
      ).toBeGreaterThan(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it("says so when the backend cannot answer", async () => {
    stubUsage(() => json({ detail: "boom" }, 500));
    render(<UsagePage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load the usage");
  });

  it("keeps the colours of the models when the range changes the order they come in", async () => {
    const user = userEvent.setup();
    const flipped = {
      ...forRange(BUSY, 7),
      models: [...BUSY.models].reverse(),
    };
    stubUsage((days) => json(days === "7" ? flipped : BUSY));
    render(<UsagePage />);
    await screen.findByRole("table", { name: "Models" });
    const colour = (model: string) =>
      row(screen.getByRole("table", { name: "Models" }), model)
        .querySelector("[aria-hidden]")
        ?.getAttribute("style");
    const before = [colour("claude-sonnet-5-5"), colour("openai/gpt-6-luna"), colour("qwen3:8b")];
    await user.click(screen.getByRole("button", { name: "7 days" }));
    await waitFor(() =>
      expect(bars("Spend per day").querySelectorAll("[data-day]")).toHaveLength(7),
    );
    expect([colour("claude-sonnet-5-5"), colour("openai/gpt-6-luna"), colour("qwen3:8b")]).toEqual(
      before,
    );
    expect(new Set(before).size).toBe(3);
  });

  it("speaks German when the dashboard does", async () => {
    setLanguage("de");
    stubUsage(() => json(BUSY));
    render(<UsagePage />);
    expect(await screen.findByRole("heading", { name: "Nutzung" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "30 Tage" })).toBeInTheDocument();
    expect(screen.getByText("Ausgegeben")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Modelle" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Wie Vorfälle endeten" })).toBeInTheDocument();
    expect(screen.getByText("Geschwärzte Geheimnisse")).toBeInTheDocument();
  });
});
