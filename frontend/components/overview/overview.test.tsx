import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { host, service } from "@/components/overview/fixtures";
import { OverviewView } from "@/components/overview/overview";

describe("OverviewView", () => {
  it("groups services under their host", () => {
    render(
      <OverviewView
        hosts={[
          host({ id: "demo", name: "Demo stack", service_count: 1 }),
          host({ id: "spare", name: "Spare M1", kind: "ssh", service_count: 1 }),
        ]}
        services={[
          service({ id: "demo/a", host_id: "demo", name: "shop" }),
          service({ id: "spare/b", host_id: "spare", name: "blog" }),
        ]}
      />,
    );
    const demo = screen.getByRole("region", { name: "Demo stack" });
    const spare = screen.getByRole("region", { name: "Spare M1" });
    expect(within(demo).getByText("shop")).toBeInTheDocument();
    expect(within(demo).queryByText("blog")).toBeNull();
    expect(within(spare).getByText("blog")).toBeInTheDocument();
    expect(within(demo).getByText("socket")).toBeInTheDocument();
    expect(within(spare).getByText("ssh")).toBeInTheDocument();
  });

  it("shows unreachable with the error text", () => {
    render(
      <OverviewView
        hosts={[
          host({
            reachable: false,
            error: "ssh: connection timed out",
            health: "down",
            service_count: 0,
          }),
        ]}
        services={[]}
      />,
    );
    expect(screen.getByText(/unreachable/i)).toBeInTheDocument();
    expect(screen.getByText(/ssh: connection timed out/)).toBeInTheDocument();
  });

  it("shows the empty state with a docs link", () => {
    render(<OverviewView hosts={[]} services={[]} />);
    expect(screen.getByText(/No hosts configured/)).toBeInTheDocument();
    expect(screen.getByText(/config\/hosts\.yaml/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /setup/i })).toHaveAttribute(
      "href",
      expect.stringContaining("github.com/DannyKuk/bleep"),
    );
  });
});
