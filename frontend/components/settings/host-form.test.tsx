import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HostForm, RemoveHost } from "@/components/settings/host-form";
import { json, sentBody, sshHost, stubBackend } from "@/components/settings/fixtures";
import type { HostSettingsView } from "@/lib/settings-types";

const OFFER = {
  offer: "offer-1",
  address: "box.lan",
  port: 22,
  fingerprint: "SHA256:abcDEF123",
  key_type: "ssh-ed25519",
};
const PRIVATE_KEY =
  "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret-body\n-----END OPENSSH PRIVATE KEY-----";

afterEach(() => vi.useRealTimers());

function dashboardHost(): HostSettingsView {
  const host = sshHost({ id: "box", name: "Box", rules: [], services: {} });
  return {
    ...host,
    connection: {
      ...host.connection,
      source: "dashboard",
      address: "box.lan",
      user: "deploy",
      key_file: null,
      known_hosts_file: null,
      ssh_key: { env: null, file: null, set: true },
      host_key: { env: null, file: null, set: true },
      version: "v-box",
    },
  };
}

async function fillNewHost() {
  await userEvent.type(screen.getByLabelText("Name"), "Box");
  expect(screen.getByLabelText("ID")).toHaveValue("box");
  await userEvent.type(screen.getByLabelText("Address"), "box.lan");
  await userEvent.type(screen.getByLabelText("User"), "deploy");
  await userEvent.click(screen.getByLabelText("SSH private key"));
  await userEvent.paste(PRIVATE_KEY);
}

const save = () => screen.getByRole("button", { name: "Add host" });
const check = () => screen.getByRole("button", { name: "Check the host key" });

describe("HostForm: adding", () => {
  it("cannot save until the fingerprint is checked and ticked, then sends the offer and empties the key", async () => {
    const fetchMock = stubBackend({
      "POST /api/settings/hosts/host-key": json(OFFER),
      "POST /api/settings/hosts": json({}, 201),
    });
    const onChanged = vi.fn(async () => undefined);
    render(<HostForm onChanged={onChanged} />);
    await fillNewHost();
    expect(save()).toBeDisabled();

    await userEvent.click(check());
    expect(await screen.findByText("SHA256:abcDEF123")).toBeInTheDocument();
    expect(screen.getByText(/ssh-keygen -lf/)).toBeInTheDocument();
    expect(save()).toBeDisabled();

    await userEvent.click(screen.getByLabelText(/matches the one on the server/));
    expect(save()).toBeEnabled();
    await userEvent.click(save());

    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(sentBody(fetchMock, "POST", 0)).toEqual({ address: "box.lan", port: 22 });
    expect(sentBody(fetchMock, "POST", 1)).toEqual({
      id: "box",
      name: "Box",
      address: "box.lan",
      port: 22,
      user: "deploy",
      docker_socket: "/var/run/docker.sock",
      ssh_key: PRIVATE_KEY,
      host_key: { offer: "offer-1", fingerprint: "SHA256:abcDEF123" },
    });
    expect(screen.getByLabelText("SSH private key")).toHaveValue("");
    expect(screen.getByLabelText("SSH private key")).toHaveAttribute("autocomplete", "off");
    expect(screen.getByLabelText("SSH private key")).toHaveAttribute("spellcheck", "false");
    expect(document.body.textContent).not.toContain("secret-body");
  });

  it("shows the one generic error for a failed fetch and the busy answer plainly", async () => {
    stubBackend({
      "POST /api/settings/hosts/host-key": [
        json({ detail: "Could not get an SSH host key from that address." }, 502),
        json({ detail: "Another host key check is running; try again in a moment." }, 429),
      ],
    });
    render(<HostForm onChanged={vi.fn()} />);
    await fillNewHost();
    await userEvent.click(check());
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not get an SSH host key from that address.",
    );
    await userEvent.click(check());
    expect(await screen.findByText(/Another host key check is running/)).toBeInTheDocument();
    expect(screen.queryByText("SHA256:abcDEF123")).toBeNull();
  });

  it("asks again when the address moves after the check", async () => {
    stubBackend({ "POST /api/settings/hosts/host-key": json(OFFER) });
    render(<HostForm onChanged={vi.fn()} />);
    await fillNewHost();
    await userEvent.click(check());
    await userEvent.click(await screen.findByLabelText(/matches the one on the server/));
    expect(save()).toBeEnabled();

    await userEvent.type(screen.getByLabelText("Address"), "x");
    expect(save()).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("Check the host key again");
    expect(screen.queryByText("SHA256:abcDEF123")).toBeNull();
  });

  it("asks again when the offer is older than ten minutes", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    stubBackend({ "POST /api/settings/hosts/host-key": json(OFFER) });
    render(<HostForm onChanged={vi.fn()} />);
    await fillNewHost();
    await userEvent.click(check());
    await userEvent.click(await screen.findByLabelText(/matches the one on the server/));
    expect(save()).toBeEnabled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10 * 60 * 1000 + 1);
    });
    expect(save()).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("offer expired");
  });

  it("keeps what was typed and shows the reason when the save is refused", async () => {
    stubBackend({
      "POST /api/settings/hosts/host-key": json(OFFER),
      "POST /api/settings/hosts": json({ detail: "a host with that id exists" }, 409),
    });
    render(<HostForm onChanged={vi.fn()} />);
    await fillNewHost();
    await userEvent.click(check());
    await userEvent.click(await screen.findByLabelText(/matches the one on the server/));
    await userEvent.click(save());
    expect(await screen.findByText("a host with that id exists")).toBeInTheDocument();
    expect(screen.getByLabelText("SSH private key")).toHaveValue(PRIVATE_KEY);
  });
});

describe("HostForm: editing", () => {
  it("warns that a new address clears the key, and sends the version in If-Match", async () => {
    const fetchMock = stubBackend({ "PUT /api/settings/hosts/box": json({}) });
    const onChanged = vi.fn(async () => undefined);
    render(<HostForm host={dashboardHost()} onChanged={onChanged} />);
    expect(screen.queryByRole("note")).toBeNull();
    expect(screen.getByLabelText("SSH private key", { exact: false })).toHaveValue("");

    await userEvent.type(screen.getByLabelText("Address"), "2");
    expect(screen.getByRole("note")).toHaveTextContent("clears this host's stored SSH key");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    const [, init] = fetchMock.mock.calls[0];
    expect(init?.headers).toMatchObject({ "if-match": '"v-box"' });
    const body = sentBody(fetchMock, "PUT");
    expect(body.address).toBe("box.lan2");
    expect(body).not.toHaveProperty("ssh_key");
    expect(body).not.toHaveProperty("host_key");
  });

  it("tells a stale page to reload, and reads the settings again", async () => {
    stubBackend({
      "PUT /api/settings/hosts/box": json(
        {
          detail: "the host changed since this version was read; nothing was saved",
          version: "v2",
        },
        409,
      ),
    });
    const onChanged = vi.fn(async () => undefined);
    render(<HostForm host={dashboardHost()} onChanged={onChanged} />);
    await userEvent.type(screen.getByLabelText("Name"), "!");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This host changed since this page read it; reload the page.",
    );
    expect(onChanged).toHaveBeenCalledTimes(1);
  });
});

describe("RemoveHost", () => {
  it("takes a second inline click, warns about open incidents, and sends the version", async () => {
    const fetchMock = stubBackend({
      "DELETE /api/settings/hosts/box": new Response(null, { status: 204 }),
    });
    const onChanged = vi.fn(async () => undefined);
    const confirm = vi.spyOn(window, "confirm");
    render(<RemoveHost host={dashboardHost()} onChanged={onChanged} />);

    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(screen.getByText(/open incidents are closed with a notice/)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Yes, remove this host" }));

    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(fetchMock.mock.calls[0][1]?.headers).toMatchObject({ "if-match": '"v-box"' });
    expect(confirm).not.toHaveBeenCalled();
  });

  it("can be cancelled before anything is sent", async () => {
    const fetchMock = stubBackend({});
    render(<RemoveHost host={dashboardHost()} onChanged={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: "Remove" })).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
