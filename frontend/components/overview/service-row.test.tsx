import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { service } from "@/components/overview/fixtures";
import { ServiceRow } from "@/components/overview/service-row";

describe("ServiceRow", () => {
  it("shows name, source, containers with state and health, and URL status", () => {
    render(
      <ServiceRow
        service={service({
          urls: [
            {
              url: "http://a.test/",
              status: "failing",
              last_checked: null,
              status_code: 502,
              latency_ms: null,
            },
            {
              url: "http://b.test/",
              status: "unknown",
              last_checked: null,
              status_code: null,
              latency_ms: null,
            },
          ],
        })}
      />,
    );
    expect(screen.getByText("shop")).toBeInTheDocument();
    expect(screen.getByText("compose")).toBeInTheDocument();
    expect(screen.getByText("shop-web-1")).toBeInTheDocument();
    expect(screen.getByText(/running/)).toBeInTheDocument();
    expect(screen.getByText(/healthy/)).toBeInTheDocument();
    expect(screen.getByText("http://a.test/")).toBeInTheDocument();
    expect(screen.getByText("Failing")).toBeInTheDocument();
    expect(screen.getByText("Not checked yet")).toBeInTheDocument();
  });

  it("shows when a URL was last checked, and says so when that check is old", () => {
    render(
      <ServiceRow
        service={service({
          urls: [
            {
              url: "http://old.test/",
              status: "unknown",
              last_checked: "2026-11-02T10:00:00Z",
              status_code: 200,
              latency_ms: 12,
            },
          ],
        })}
      />,
    );
    expect(screen.getByText("No recent check")).toBeInTheDocument();
    expect(screen.getByText(/200 · 12 ms · checked /)).toBeInTheDocument();
  });

  it("links the open incident", () => {
    render(<ServiceRow service={service({ health: "down", open_incident_id: "inc_42" })} />);
    expect(screen.getByRole("link", { name: /open incident/i })).toHaveAttribute(
      "href",
      "/incidents/inc_42",
    );
  });

  it("shows no incident link when there is none", () => {
    render(<ServiceRow service={service()} />);
    expect(screen.queryByRole("link", { name: /incident/i })).toBeNull();
  });

  it("shows a stopped service grey, and its cleanly exited container as stopped, not down", () => {
    const base = service().containers[0];
    render(
      <ServiceRow
        service={service({
          health: "stopped",
          containers: [{ ...base, state: "exited", health: "none", exit_code: 0 }],
        })}
      />,
    );
    expect(screen.getByText("Stopped").closest("[data-health]")).toHaveAttribute(
      "data-health",
      "stopped",
    );
    expect(screen.getByText(/exited/).className).toContain("text-health-stopped");
  });
});
