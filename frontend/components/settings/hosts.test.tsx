import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { sshHost } from "@/components/settings/fixtures";
import { HostSettings, withHostRules, withService } from "@/components/settings/hosts";

describe("withService", () => {
  it("changes one field and keeps the service's and the host's others", () => {
    const host = {
      rules: ["host rule"],
      services: {
        "compose:shop": { rules: ["r"] },
        "compose:worker": { urls: [] },
      },
    };
    expect(withService(host, "compose:shop", { urls: [] })).toEqual({
      rules: ["host rule"],
      services: {
        "compose:shop": { rules: ["r"], urls: [] },
        "compose:worker": { urls: [] },
      },
    });
  });

  it("drops a field set to undefined and a service left with nothing", () => {
    const host = { services: { "compose:shop": { rules: ["r"] } } };
    expect(withService(host, "compose:shop", { rules: undefined })).toEqual({ services: {} });
    expect(withService(undefined, "compose:shop", { rules: ["new"] })).toEqual({
      services: { "compose:shop": { rules: ["new"] } },
    });
  });
});

describe("withHostRules", () => {
  it("replaces the host's rules or goes back to the hosts file's", () => {
    const host = { rules: ["old"], services: {} };
    expect(withHostRules(host, ["new"])).toEqual({ rules: ["new"], services: {} });
    expect(withHostRules(host)).toEqual({ services: {} });
  });
});

describe("HostSettings", () => {
  it("shows a socket host's endpoint and no ssh fields", () => {
    const host = sshHost({
      connection: {
        kind: "socket",
        address: null,
        user: null,
        port: null,
        docker_socket: null,
        endpoint: "tcp://socket-proxy:2375",
        key_file: null,
        known_hosts_file: null,
      },
      services: {},
    });
    render(<HostSettings host={host} overrides={undefined} services={[]} save={vi.fn()} />);
    expect(screen.getByText("tcp://socket-proxy:2375")).toBeInTheDocument();
    expect(screen.queryByText("SSH key")).toBeNull();
    expect(screen.getByText("Bleep sees no services on this host yet.")).toBeInTheDocument();
  });

  it("edits a service's rules and brings the hosts file's back", async () => {
    const save = vi.fn(async () => undefined);
    const overrides = {
      services: { "compose:shop": { rules: ["The db is shared with billing."] } },
    };
    render(
      <HostSettings
        host={sshHost()}
        overrides={overrides}
        services={[{ key: "compose:shop", name: "shop" }]}
        save={save}
      />,
    );
    const shop = screen.getByRole("region", { name: "Service shop" });
    expect(within(shop).getByText("compose:shop")).toBeInTheDocument();

    const rule = within(shop).getByRole("textbox", { name: "Rules for shop: entry 1" });
    await userEvent.type(rule, " Never restart it.");
    await userEvent.click(within(shop).getByRole("button", { name: "Save" }));
    expect(save).toHaveBeenLastCalledWith({
      services: { "compose:shop": { rules: ["The db is shared with billing. Never restart it."] } },
    });

    await userEvent.click(within(shop).getByRole("button", { name: "Use the hosts file's" }));
    expect(save).toHaveBeenLastCalledWith({ services: {} });
  });

  it("leaves the length of a rule to the API instead of cutting it off as it is typed", async () => {
    const save = vi.fn(async () => undefined);
    render(<HostSettings host={sshHost()} overrides={undefined} services={[]} save={save} />);
    const long = "x".repeat(600);
    const add = screen.getByPlaceholderText("Add a rule for this host");
    expect(add).not.toHaveAttribute("maxLength");
    await userEvent.click(add);
    await userEvent.paste(long);
    expect(add).toHaveValue(long);
  });

  it("shows a URL row left empty as invalid and sends nothing", async () => {
    const save = vi.fn(async () => undefined);
    render(
      <HostSettings
        host={sshHost()}
        overrides={undefined}
        services={[{ key: "compose:shop", name: "shop" }]}
        save={save}
      />,
    );
    const shop = screen.getByRole("region", { name: "Service shop" });
    await userEvent.click(within(shop).getByRole("button", { name: "Add a URL" }));
    await userEvent.click(within(shop).getByRole("button", { name: "Save" }));

    expect(save).not.toHaveBeenCalled();
    expect(within(shop).getByRole("textbox", { name: "URL 2" })).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(within(shop).getByRole("alert")).toHaveTextContent(
      "URL 2: enter an address, or remove the row",
    );
  });
});
