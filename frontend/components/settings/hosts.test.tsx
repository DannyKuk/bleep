import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { sshHost } from "@/components/settings/fixtures";
import { HostSettings, withHostRules, withMaySleep } from "@/components/settings/hosts";

describe("withHostRules", () => {
  it("replaces the host's rules or goes back to the hosts file's", () => {
    const host = { rules: ["old"], services: {} };
    expect(withHostRules(host, ["new"])).toEqual({ rules: ["new"], services: {} });
    expect(withHostRules(host)).toEqual({ services: {} });
  });
});

describe("withMaySleep", () => {
  it("sets may_sleep and keeps the other overrides, or goes back to the hosts file's", () => {
    const host = { rules: ["r"], may_sleep: false };
    expect(withMaySleep(host, true)).toEqual({ rules: ["r"], may_sleep: true });
    expect(withMaySleep(host)).toEqual({ rules: ["r"] });
  });
});

describe("HostSettings", () => {
  it("saves that the host may sleep with its other overrides kept", async () => {
    const save = vi.fn(async () => undefined);
    const overrides = { rules: ["dashboard rule"] };
    render(<HostSettings host={sshHost()} overrides={overrides} save={save} />);
    const box = screen.getByRole("checkbox", { name: "This host may sleep" });
    expect(box).not.toBeChecked();
    await userEvent.click(box);
    expect(save).toHaveBeenCalledWith({ rules: ["dashboard rule"], may_sleep: true });
  });

  it("offers the hosts file's choice back once the dashboard changed it", async () => {
    const save = vi.fn(async () => undefined);
    render(
      <HostSettings
        host={sshHost({ may_sleep: true })}
        overrides={{ may_sleep: true }}
        save={save}
      />,
    );
    expect(screen.getByRole("checkbox", { name: "This host may sleep" })).toBeChecked();
    const [reset] = screen.getAllByRole("button", { name: "Use the hosts file's" });
    await userEvent.click(reset);
    expect(save).toHaveBeenCalledWith({});
  });

  it("shows a socket host's endpoint and no ssh fields", () => {
    const host = sshHost({
      connection: {
        source: "file",
        kind: "socket",
        address: null,
        user: null,
        port: null,
        docker_socket: null,
        endpoint: "tcp://socket-proxy:2375",
        key_file: null,
        known_hosts_file: null,
        ssh_key: null,
        host_key: null,
        version: null,
      },
      services: {},
    });
    render(<HostSettings host={host} overrides={undefined} save={vi.fn()} />);
    expect(screen.getByText("tcp://socket-proxy:2375")).toBeInTheDocument();
    expect(screen.queryByText("SSH key")).toBeNull();
  });

  it("leaves the length of a rule to the API instead of cutting it off as it is typed", async () => {
    const save = vi.fn(async () => undefined);
    render(<HostSettings host={sshHost()} overrides={undefined} save={save} />);
    const long = "x".repeat(600);
    const add = screen.getByPlaceholderText("Add a rule for this host");
    expect(add).not.toHaveAttribute("maxLength");
    await userEvent.click(add);
    await userEvent.paste(long);
    expect(add).toHaveValue(long);
  });
});
