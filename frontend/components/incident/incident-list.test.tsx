import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { incident } from "@/components/incident/fixtures";
import { IncidentListView } from "@/components/incident/incident-list";

// Two days after the fixtures' incidents opened, so their failed run is long over.
const NOW = Date.parse("2026-11-04T18:00:00Z");

describe("IncidentListView", () => {
  it("links each incident to its page with its status and subject", () => {
    render(
      <IncidentListView
        incidents={[
          incident(),
          incident({
            id: "inc-2",
            title: "Disk 93% full",
            status: "resolved",
            subject: { kind: "host", host_id: "spare", service_id: null },
          }),
        ]}
        now={NOW}
      />,
    );
    expect(screen.getByRole("link", { name: /shop-web-1 died/ })).toHaveAttribute(
      "href",
      "/incidents/inc-1",
    );
    expect(screen.getByText("Needs your yes")).toBeInTheDocument();
    expect(screen.getByText("demo/compose:shop")).toBeInTheDocument();
    expect(screen.getByText("Resolved", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText("host spare")).toBeInTheDocument();
  });

  it("puts the short name in front of the title, in mono", () => {
    render(<IncidentListView incidents={[incident({ short_name: "shop #14" })]} now={NOW} />);
    const row = screen.getByRole("link", { name: /shop-web-1 died/ });
    expect(row).toHaveTextContent("shop #14 · shop-web-1 died");
    expect(within(row).getByText("shop #14")).toHaveClass("font-mono");
  });

  it("shows only the title for an incident without a short name", () => {
    render(<IncidentListView incidents={[incident()]} now={NOW} />);
    const row = screen.getByRole("link", { name: /shop-web-1 died/ });
    expect(within(row).getByText("shop-web-1 died")).toHaveTextContent(/^shop-web-1 died$/);
    expect(row).not.toHaveTextContent("·");
  });

  it("shows a short name that looks like markup as text", () => {
    const name = '"<img src=x onerror=alert(1)>" #2';
    const { container } = render(
      <IncidentListView incidents={[incident({ short_name: name })]} now={NOW} />,
    );
    expect(screen.getByText(name)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });

  it("finds an incident by its short name, ignoring case (#397)", () => {
    const incidents = [
      incident({ short_name: "shop #14" }),
      incident({ id: "inc-2", short_name: "shop #15", title: "Disk 93% full" }),
      incident({ id: "inc-3", title: "Worker crashed" }),
    ];
    render(<IncidentListView incidents={incidents} now={NOW} />);
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "shop #14" } });
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(screen.getByRole("link")).toHaveTextContent("shop #14 · shop-web-1 died");
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "SHOP #1" } });
    expect(screen.getAllByRole("link")).toHaveLength(2);
  });

  it("shows the empty state", () => {
    render(<IncidentListView incidents={[]} now={NOW} />);
    expect(screen.getByText(/No incidents yet/)).toBeInTheDocument();
  });

  const mixed = () => [
    incident(),
    incident({ id: "inc-2", title: "Disk 93% full", status: "investigating" }),
    incident({
      id: "inc-3",
      title: "Cache restarted",
      status: "resolved",
      closed_at: "2026-11-02T18:04:00Z",
    }),
    incident({ id: "inc-4", title: "Worker crashed", status: "failed" }),
  ];

  it("groups the rows and marks the one waiting for the human", () => {
    render(<IncidentListView incidents={mixed()} now={NOW} />);
    expect(screen.getByRole("heading", { name: "1 needs you, I am on 1." })).toBeInTheDocument();
    const needs = screen.getByRole("list", { name: "Needs you" });
    expect(within(needs).getByRole("link", { name: /shop-web-1 died/ })).toHaveClass("border-down");
    const earlier = screen.getByRole("list", { name: "Earlier" });
    expect(within(earlier).getAllByRole("link")).toHaveLength(2);
    expect(within(earlier).getByText(/4 min/)).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "I am on it" })).toBeInTheDocument();
  });

  it("puts a run that failed within a day under Needs you and counts it", () => {
    const failed = incident({
      id: "inc-5",
      title: "Restart did not help",
      status: "failed",
      closed_at: "2026-11-04T16:00:00Z",
    });
    render(<IncidentListView incidents={[failed, ...mixed()]} now={NOW} />);
    expect(screen.getByRole("heading", { name: "2 need you, I am on 1." })).toBeInTheDocument();
    const needs = screen.getByRole("list", { name: "Needs you" });
    expect(within(needs).getByRole("link", { name: /Restart did not help/ })).toHaveClass(
      "border-down",
    );
    expect(within(screen.getByRole("list", { name: "Earlier" })).getByText("Worker crashed"));

    fireEvent.click(screen.getByRole("button", { name: "Closed or failed" }));
    expect(screen.getByText("Restart did not help")).toBeInTheDocument();
  });

  it("filters by chip and by search over title and subject", () => {
    render(<IncidentListView incidents={mixed()} now={NOW} />);
    fireEvent.click(screen.getByRole("button", { name: "Closed or failed" }));
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(screen.getByText("Worker crashed")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "All" }));
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "DEMO/compose" } });
    expect(screen.getAllByRole("link")).toHaveLength(4);
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "disk" } });
    expect(screen.getAllByRole("link")).toHaveLength(1);
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "nothing" } });
    expect(screen.getByText("No incident matches.")).toBeInTheDocument();
  });
});

describe("IncidentListView with repeats", () => {
  it("shows a group of repeats as one row with its count", () => {
    const rows = [
      {
        ...incident({ id: "r2", status: "closed", fold_of: "first", title: "shop died" }),
        repeats: 3,
      },
      {
        ...incident({ id: "r1", status: "closed", fold_of: "first", title: "shop died" }),
        repeats: 3,
      },
      { ...incident({ id: "first", status: "closed", title: "shop died" }), repeats: 3 },
    ];
    render(<IncidentListView incidents={rows} now={NOW} />);
    const links = screen.getAllByRole("link");
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", "/incidents/r2");
    expect(links[0]).toHaveTextContent("Seen 3 times");
  });
});
