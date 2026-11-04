import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { service } from "@/components/overview/fixtures";
import { ContainerChip, UrlItem } from "@/components/overview/service-row";
import type { UrlStatus } from "@/lib/api-types";

const container = service().containers[0];

function url(overrides: Partial<UrlStatus> = {}): UrlStatus {
  return {
    url: "http://a.test/",
    status: "ok",
    last_checked: null,
    status_code: null,
    latency_ms: null,
    ...overrides,
  };
}

function renderUrls(...urls: UrlStatus[]) {
  render(
    <ul>
      {urls.map((item) => (
        <UrlItem key={item.url} url={item} />
      ))}
    </ul>,
  );
}

describe("ContainerChip", () => {
  it("shows the container's name with its state and health", () => {
    render(
      <ul>
        <ContainerChip container={container} />
      </ul>,
    );
    expect(screen.getByText("shop-web-1")).toBeInTheDocument();
    expect(screen.getByText("running, healthy")).toBeInTheDocument();
  });

  it("shows a cleanly exited container as stopped, not down", () => {
    render(
      <ul>
        <ContainerChip
          container={{ ...container, state: "exited", health: "none", exit_code: 0 }}
        />
      </ul>,
    );
    expect(screen.getByText("exited").className).toContain("text-health-stopped");
  });
});

describe("UrlItem", () => {
  it("shows each URL with its status", () => {
    renderUrls(
      url({ url: "http://a.test/", status: "failing", status_code: 502 }),
      url({ url: "http://b.test/", status: "unknown" }),
    );
    expect(screen.getByText("http://a.test/")).toBeInTheDocument();
    expect(screen.getByText("Failing")).toBeInTheDocument();
    expect(screen.getByText("Not checked yet")).toBeInTheDocument();
  });

  it("shows when a URL was last checked, and says so when that check is old", () => {
    renderUrls(
      url({
        url: "http://old.test/",
        status: "unknown",
        last_checked: "2026-11-02T10:00:00Z",
        status_code: 200,
        latency_ms: 12,
      }),
    );
    expect(screen.getByText("No recent check")).toBeInTheDocument();
    expect(screen.getByText(/200 · 12 ms · checked /)).toBeInTheDocument();
  });

  it("shows a failing URL with the time of its check", () => {
    renderUrls(
      url({
        url: "http://bad.test/",
        status: "failing",
        last_checked: "2026-11-02T10:00:00Z",
        status_code: 500,
        latency_ms: 30,
      }),
    );
    expect(screen.getByText("Failing")).toBeInTheDocument();
    expect(screen.getByText(/500 · 30 ms · checked /)).toBeInTheDocument();
  });
});
