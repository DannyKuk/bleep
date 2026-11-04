import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { OverallProvider } from "@/components/overall-state";
import { host, service } from "@/components/overview/fixtures";
import { Overview, OverviewView } from "@/components/overview/overview";
import { json, stubBackend } from "@/components/settings/fixtures";

const NOW = Date.parse("2026-11-02T19:00:00Z");
const OFFER = {
  offer: "offer-1",
  address: "box.lan",
  port: 22,
  fingerprint: "SHA256:abcDEF123",
  key_type: "ssh-ed25519",
};
const PRIVATE_KEY =
  "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret-body\n-----END OPENSSH PRIVATE KEY-----";

const plus = () =>
  within(screen.getByRole("region", { name: "Your hosts" })).getByRole("button", {
    name: "Add a host",
  });
const dialog = () => screen.queryByRole("dialog", { name: "Add a host" });

async function fillAndConfirm() {
  const form = within(dialog()!);
  await userEvent.type(form.getByLabelText("Name"), "Box");
  await userEvent.type(form.getByLabelText("Address"), "box.lan");
  await userEvent.type(form.getByLabelText("User"), "deploy");
  await userEvent.click(form.getByLabelText("SSH private key"));
  await userEvent.paste(PRIVATE_KEY);
  await userEvent.click(form.getByRole("button", { name: "Check the host key" }));
  await userEvent.click(await form.findByLabelText(/matches the one on the server/));
}

describe("Adding a host from the overview", () => {
  it("opens the form from the + at the end of the hosts row, and Cancel and Esc close it", async () => {
    render(<OverviewView hosts={[host()]} services={[service()]} incidents={[]} now={NOW} />);
    expect(dialog()).toBeNull();

    await userEvent.click(plus());
    expect(dialog()).toHaveTextContent("Only SSH hosts can be added here.");
    expect(within(dialog()!).getByLabelText("SSH private key")).toHaveValue("");
    await userEvent.click(within(dialog()!).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(dialog()).toBeNull());

    await userEvent.click(plus());
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(dialog()).toBeNull());
  });

  it("forgets a typed key when the dialog is closed without saving", async () => {
    render(<OverviewView hosts={[host()]} services={[service()]} incidents={[]} now={NOW} />);
    await userEvent.click(plus());
    await userEvent.click(within(dialog()!).getByLabelText("SSH private key"));
    await userEvent.paste(PRIVATE_KEY);
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(dialog()).toBeNull());

    await userEvent.click(plus());
    expect(within(dialog()!).getByLabelText("SSH private key")).toHaveValue("");
    expect(document.body.textContent).not.toContain("secret-body");
  });

  it("offers the dialog from the empty state too", async () => {
    render(<OverviewView hosts={[]} services={[]} incidents={[]} now={NOW} />);
    await userEvent.click(screen.getByRole("button", { name: "Add a host" }));
    expect(dialog()).toBeInTheDocument();
  });

  it("closes after the host is added and shows it without waiting for the next poll", async () => {
    const added = host({ id: "box", name: "Box", kind: "ssh" });
    const fetchMock = stubBackend({
      "GET /api/incidents?limit=200": json([]),
      "GET /api/hosts": [json([host()]), json([host(), added])],
      "GET /api/services": json([service()]),
      "POST /api/settings/hosts/host-key": json(OFFER),
      "POST /api/settings/hosts": json({}, 201),
    });
    render(
      <OverallProvider>
        <Overview />
      </OverallProvider>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "Add a host" }));
    await fillAndConfirm();
    await userEvent.click(within(dialog()!).getByRole("button", { name: "Add host" }));

    await waitFor(() => expect(dialog()).toBeNull());
    expect(await screen.findByRole("heading", { name: /Box/ })).toBeInTheDocument();
    const hostReads = fetchMock.mock.calls.filter(([input]) => String(input) === "/api/hosts");
    expect(hostReads).toHaveLength(2);
  });

  it("stays open while the save is in flight, even on Esc", async () => {
    let answer: (response: Response) => void = () => undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async (input, init) => {
        const key = `${init?.method ?? "GET"} ${String(input)}`;
        if (key === "POST /api/settings/hosts/host-key") return json(OFFER);
        if (key === "POST /api/settings/hosts") {
          return new Promise<Response>((resolve) => (answer = resolve));
        }
        return json({ detail: "no stub" }, 500);
      }),
    );
    const onHostAdded = vi.fn(async () => undefined);
    render(
      <OverviewView
        hosts={[host()]}
        services={[service()]}
        incidents={[]}
        now={NOW}
        onHostAdded={onHostAdded}
      />,
    );
    await userEvent.click(plus());
    await fillAndConfirm();
    await userEvent.click(within(dialog()!).getByRole("button", { name: "Add host" }));
    await userEvent.keyboard("{Escape}");
    expect(dialog()).toBeInTheDocument();

    answer(json({}, 201));
    await waitFor(() => expect(dialog()).toBeNull());
    expect(onHostAdded).toHaveBeenCalledOnce();
  });
});
