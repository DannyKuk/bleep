import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { sshHost } from "@/components/settings/fixtures";
import { HostSettings, withHostRules } from "@/components/settings/hosts";

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
