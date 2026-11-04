import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { json } from "@/components/dev/fixtures";
import { HomeLogo } from "@/components/home-logo";
import { incident } from "@/components/incident/fixtures";
import { OverallProvider, useOverall } from "@/components/overall-state";
import { host } from "@/components/overview/fixtures";
import type { HostView, Incident } from "@/lib/api-types";

function stubReads(incidents: Incident[], hosts: HostView[]) {
  const fetchMock = vi.fn<typeof fetch>(async (input) =>
    String(input).startsWith("/api/incidents") ? json(incidents) : json(hosts),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function StateLine() {
  const { state, incidents } = useOverall();
  return <p>{`${state ?? "none"} from ${incidents?.length ?? 0}`}</p>;
}

function renderHeader() {
  const { container } = render(
    <OverallProvider>
      <HomeLogo />
      <StateLine />
    </OverallProvider>,
  );
  return () => container.querySelector("svg")!;
}

describe("HomeLogo", () => {
  it("is the head-only Bleep linking home", async () => {
    stubReads([], [host()]);
    const svg = renderHeader();
    const link = screen.getByRole("link", { name: /Bleep/ });
    expect(link).toHaveAttribute("href", "/");
    expect(svg()).toHaveAttribute("data-figure", "head");
    expect(await screen.findByText("green from 0")).toBeInTheDocument();
  });

  it.each([
    [[incident({ status: "awaiting_approval" })], [host()], "red", "needs_you"],
    [[incident({ status: "investigating" })], [host()], "amber", "working"],
    [[], [host({ reachable: false })], "amber", "working"],
    [[incident({ status: "resolved" })], [host()], "green", "all_good"],
  ])("takes the overall state's colour (%#)", async (incidents, hosts, state, mood) => {
    stubReads(incidents, hosts);
    const svg = renderHeader();
    await vi.waitFor(() =>
      expect(screen.getByRole("link", { name: /Bleep/ })).toHaveAttribute("data-state", state),
    );
    expect(svg()).toHaveAttribute("data-mood", mood);
  });

  it("shows grey eyes until the first read answers", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => {})),
    );
    const svg = renderHeader();
    expect(svg()).toHaveAttribute("data-mood", "not_sure");
    expect(screen.getByRole("link", { name: /checking/ })).toHaveAttribute("data-state", "unknown");
  });
});

describe("OverallProvider", () => {
  it("reads the incidents and the hosts once per tick for every reader", async () => {
    const fetchMock = stubReads([incident({ status: "open" })], [host()]);
    renderHeader();
    expect(await screen.findByText("amber from 1")).toBeInTheDocument();
    const paths = fetchMock.mock.calls.map(([input]) => String(input));
    expect(paths).toEqual(["/api/incidents?limit=200", "/api/hosts"]);
  });

  it("refuses a reader outside the app layout", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<StateLine />)).toThrow(/OverallProvider/);
  });
});
