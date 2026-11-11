import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  invalid,
  json,
  plugin,
  secretView,
  secretViews,
  sentBody,
  settingsView,
  stubBackend,
  telegram,
} from "@/components/settings/fixtures";
import { SecretField } from "@/components/settings/secret-field";
import { SettingsPage } from "@/components/settings/settings-page";
import type { SecretView } from "@/lib/secrets-api";
import { MESSAGES } from "@/lib/i18n";

const TOKEN = "tok-9f8e7d6c5b4a";

const address = (over: Partial<SecretView> = {}) =>
  secretView("COOLIFY_URL", { kind: "address", carries: "COOLIFY_TOKEN", ...over });
const withAddress = (version = "a1") =>
  address({ set: true, source: "dashboard", value: "https://coolify.example.com", version });

function backend(secrets: SecretView[], routes: Record<string, Response | Response[]> = {}) {
  return stubBackend({
    "GET /api/settings": json(settingsView()),
    "GET /api/services": json([]),
    "GET /api/plugins": json([plugin()]),
    "GET /api/telegram/status": json(telegram()),
    "GET /api/secrets": json(secrets),
    ...routes,
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("SecretField", () => {
  const actions = () => ({
    set: vi.fn(async () => undefined),
    clear: vi.fn(async () => undefined),
  });

  it("takes a secret in a password input that never autocompletes, and empties it after the save", async () => {
    const a = actions();
    render(<SecretField view={secretView("GITHUB_TOKEN")} actions={a} />);
    const input = screen.getByLabelText("Value for GITHUB_TOKEN");
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveAttribute("autocomplete", "off");
    expect(input).toHaveValue("");
    await userEvent.type(input, TOKEN);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(a.set).toHaveBeenCalledWith("GITHUB_TOKEN", TOKEN, undefined);
    expect(await screen.findByLabelText("Value for GITHUB_TOKEN")).toHaveValue("");
    expect(document.body).not.toHaveTextContent(TOKEN);
  });

  it("tells a model server's address it may be private, and a plugin's that it may not", () => {
    const model = secretView("EMBEDDINGS_BASE_URL", {
      kind: "address",
      carries: "EMBEDDINGS_API_KEY",
      private: true,
    });
    const { unmount } = render(<SecretField view={model} actions={actions()} />);
    expect(screen.getByText(/may be private/)).toHaveTextContent(
      "A new or cleared address clears EMBEDDINGS_API_KEY.",
    );
    expect(screen.queryByText(/switches the plugin off/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Value for EMBEDDINGS_BASE_URL")).toHaveAttribute(
      "placeholder",
      "http://",
    );
    unmount();
    render(<SecretField view={address()} actions={actions()} />);
    expect(screen.getByText(/must be https/)).toHaveTextContent("switches the plugin off");
  });

  it("never fills the input of a secret that is set, and clears it on request", async () => {
    const a = actions();
    const set = secretView("GITHUB_TOKEN", { set: true, source: "dashboard" });
    render(<SecretField view={set} actions={a} />);
    expect(screen.getByText("Set on the dashboard")).toBeInTheDocument();
    expect(screen.getByLabelText("New value for GITHUB_TOKEN")).toHaveValue("");
    await userEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(a.clear).toHaveBeenCalledWith("GITHUB_TOKEN");
  });

  it("offers no input and no button for a value the env sets", () => {
    const owned = secretView("GITHUB_TOKEN", { set: true, source: "env", env_owned: true });
    render(<SecretField view={owned} actions={actions()} />);
    expect(screen.getByText("Set in .env")).toBeInTheDocument();
    expect(screen.getByText(/wins over the dashboard/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
    expect(document.querySelector("input")).toBeNull();
  });

  it("keeps the typed value and shows the API's reason under the field when a save fails", async () => {
    const a = actions();
    a.set.mockRejectedValueOnce(new Error("COOLIFY_URL must be https"));
    render(<SecretField view={address()} actions={a} />);
    const input = screen.getByRole("textbox", { name: "Value for COOLIFY_URL" });
    await userEvent.type(input, "http://coolify.lan");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("COOLIFY_URL must be https");
    expect(input).toHaveValue("http://coolify.lan");
  });

  it("shows a hand-edited address as set but not valid, with the controls to enter it again", async () => {
    const a = actions();
    const bad = address({ set: true, source: "dashboard", invalid: true });
    render(<SecretField view={bad} actions={a} />);
    expect(screen.getByText("Set, but not valid: enter it again")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear" })).toBeInTheDocument();
    await userEvent.type(
      screen.getByLabelText("New value for COOLIFY_URL"),
      "https://c.example.com",
    );
    await userEvent.click(screen.getByRole("button", { name: "Replace" }));
    expect(a.set).toHaveBeenCalledWith("COOLIFY_URL", "https://c.example.com", undefined);
  });

  it("warns that a new address clears its token, and shows only the origin that is set", () => {
    render(<SecretField view={withAddress()} actions={actions()} />);
    expect(screen.getByText("https://coolify.example.com")).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveAttribute("type", "text");
    expect(
      screen.getByText(/clears COOLIFY_TOKEN and switches the plugin off/),
    ).toBeInTheDocument();
  });

  it("gives a token no input until its address is set, and none when the env sets the address", () => {
    const token = secretView("COOLIFY_TOKEN");
    const { rerender } = render(
      <SecretField view={token} address={address()} actions={actions()} />,
    );
    expect(screen.getByText(/Set COOLIFY_URL first/)).toBeInTheDocument();
    expect(document.querySelector("input")).toBeNull();
    rerender(
      <SecretField
        view={token}
        address={address({ set: true, source: "env", env_owned: true })}
        actions={actions()}
      />,
    );
    expect(screen.getByText(/read from .env too/)).toBeInTheDocument();
    expect(document.querySelector("input")).toBeNull();
  });

  it("sends a token with the version of the address it was typed for", async () => {
    const a = actions();
    render(
      <SecretField view={secretView("COOLIFY_TOKEN")} address={withAddress("a7")} actions={a} />,
    );
    await userEvent.type(screen.getByLabelText("Value for COOLIFY_TOKEN"), TOKEN);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(a.set).toHaveBeenCalledWith("COOLIFY_TOKEN", TOKEN, "a7");
  });
});

describe("secrets on the settings page", () => {
  it("lists the secrets that no plugin owns with where each is set, and none with a value", async () => {
    backend(secretViews());
    render(<SettingsPage />);
    const key = await screen.findByRole("group", { name: "OPENROUTER_API_KEY" });
    expect(within(key).getByText("Set in .env")).toBeInTheDocument();
    expect(within(key).queryByRole("button")).toBeNull();
    const bot = screen.getByRole("group", { name: "TELEGRAM_BOT_TOKEN" });
    expect(within(bot).getByText("Missing")).toBeInTheDocument();
    expect(within(bot).getByLabelText("Value for TELEGRAM_BOT_TOKEN")).toHaveAttribute(
      "type",
      "password",
    );
    const coolify = screen.getByRole("region", { name: "Plugin Coolify" });
    expect(within(coolify).getByRole("group", { name: "COOLIFY_URL" })).toBeInTheDocument();
    expect(screen.getAllByRole("group", { name: "COOLIFY_URL" })).toHaveLength(1);
  });

  it("says a key set on the dashboard is set there, not in the env", async () => {
    backend([
      secretView("OPENROUTER_API_KEY", { set: true, source: "dashboard" }),
      ...secretViews().slice(1),
    ]);
    render(<SettingsPage />);
    const key = await screen.findByRole("group", { name: "OPENROUTER_API_KEY" });
    expect(key).toHaveTextContent("Set on the dashboard");
    expect(key).not.toHaveTextContent("Set in .env");
  });

  it("sets a key, empties the input and shows it as set without ever holding the value", async () => {
    const after = secretViews();
    after[1] = secretView("TELEGRAM_BOT_TOKEN", { set: true, source: "dashboard" });
    const fetchMock = backend(secretViews(), {
      "PUT /api/secrets/TELEGRAM_BOT_TOKEN": json(after),
    });
    render(<SettingsPage />);
    const input = await screen.findByLabelText("Value for TELEGRAM_BOT_TOKEN");
    await userEvent.type(input, TOKEN);
    await userEvent.click(
      within(screen.getByRole("group", { name: "TELEGRAM_BOT_TOKEN" })).getByRole("button", {
        name: "Save",
      }),
    );
    expect(sentBody(fetchMock, "PUT")).toEqual({ value: TOKEN });
    const bot = screen.getByRole("group", { name: "TELEGRAM_BOT_TOKEN" });
    expect(await within(bot).findByText("Set on the dashboard")).toBeInTheDocument();
    expect(within(bot).getByLabelText("New value for TELEGRAM_BOT_TOKEN")).toHaveValue("");
    expect(document.body.innerHTML).not.toContain(TOKEN);
  });

  it("sends a new address, then shows the plugin off with its token cleared", async () => {
    const on = plugin({
      enabled: true,
      switched_on: true,
      secrets: [
        { env: "COOLIFY_URL", set: true, required: true },
        { env: "COOLIFY_TOKEN", set: true, required: true },
      ],
    });
    const before = [
      withAddress("a1"),
      secretView("COOLIFY_TOKEN", { set: true, source: "dashboard" }),
    ];
    const after = [withAddress("a2"), secretView("COOLIFY_TOKEN")];
    after[0].value = "https://new.example.com";
    const off = plugin({
      secrets: [
        { env: "COOLIFY_URL", set: true, required: true },
        { env: "COOLIFY_TOKEN", set: false, required: true },
      ],
    });
    const fetchMock = stubBackend({
      "GET /api/settings": json(settingsView()),
      "GET /api/services": json([]),
      "GET /api/plugins": [json([on]), json([off])],
      "GET /api/telegram/status": json(telegram()),
      "GET /api/secrets": json(before),
      "PUT /api/secrets/COOLIFY_URL": json(after),
    });
    render(<SettingsPage />);
    const coolify = await screen.findByRole("region", { name: "Plugin Coolify" });
    expect(await within(coolify).findByText("On")).toBeInTheDocument();
    expect(
      within(coolify).getByText(/clears COOLIFY_TOKEN and switches the plugin off/),
    ).toBeVisible();

    await userEvent.type(
      within(coolify).getByLabelText("New value for COOLIFY_URL"),
      "https://new.example.com",
    );
    await userEvent.click(within(coolify).getAllByRole("button", { name: "Replace" })[0]);

    expect(sentBody(fetchMock, "PUT")).toEqual({ value: "https://new.example.com" });
    expect(await within(coolify).findByText("Off")).toBeInTheDocument();
    const token = within(coolify).getByRole("group", { name: "COOLIFY_TOKEN" });
    expect(within(token).getByText("Missing")).toBeInTheDocument();
  });

  it("reads again and says the address changed when a token is refused as stale", async () => {
    const fresh = [withAddress("a2"), secretView("COOLIFY_TOKEN")];
    const fetchMock = backend([withAddress("a1"), secretView("COOLIFY_TOKEN")], {
      "PUT /api/secrets/COOLIFY_TOKEN": json({ detail: "the address changed" }, 409),
    });
    render(<SettingsPage />);
    const token = await screen.findByLabelText("Value for COOLIFY_TOKEN");
    fetchMock.mockClear();
    stubBackend({
      "GET /api/secrets": json(fresh),
      "PUT /api/secrets/COOLIFY_TOKEN": json({ detail: "the address changed" }, 409),
    });
    await userEvent.type(token, TOKEN);
    await userEvent.click(
      within(screen.getByRole("group", { name: "COOLIFY_TOKEN" })).getByRole("button", {
        name: "Save",
      }),
    );
    const group = screen.getByRole("group", { name: "COOLIFY_TOKEN" });
    expect(await within(group).findByRole("alert")).toHaveTextContent(
      MESSAGES.en.settings.addressChanged,
    );
    expect(within(group).getByLabelText("Value for COOLIFY_TOKEN")).toHaveValue("");
  });

  it("does not claim the address changed when it could not read the secrets again", async () => {
    backend([withAddress("a1"), secretView("COOLIFY_TOKEN")], {
      "PUT /api/secrets/COOLIFY_TOKEN": json({ detail: "the address changed" }, 409),
    });
    render(<SettingsPage />);
    const input = await screen.findByLabelText("Value for COOLIFY_TOKEN");
    stubBackend({
      "GET /api/secrets": json({ detail: "Bad Gateway" }, 502),
      "PUT /api/secrets/COOLIFY_TOKEN": json({ detail: "the address changed" }, 409),
    });
    await userEvent.type(input, TOKEN);
    const group = screen.getByRole("group", { name: "COOLIFY_TOKEN" });
    await userEvent.click(within(group).getByRole("button", { name: "Save" }));
    const alert = await within(group).findByRole("alert");
    expect(alert).toHaveTextContent(MESSAGES.en.settings.addressNotChecked);
    expect(alert).not.toHaveTextContent("has been read again");
  });

  it("shows the API's refusal of a private address under the field", async () => {
    backend([address(), secretView("COOLIFY_TOKEN")], {
      "PUT /api/secrets/COOLIFY_URL": json(
        {
          detail:
            "COOLIFY_URL set on the dashboard must be https; an address on a private network can be set in .env",
        },
        422,
      ),
    });
    render(<SettingsPage />);
    await userEvent.type(
      await screen.findByLabelText("Value for COOLIFY_URL"),
      "http://coolify:8000",
    );
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    const group = screen.getByRole("group", { name: "COOLIFY_URL" });
    expect(await within(group).findByRole("alert")).toHaveTextContent("can be set in .env");
  });

  it("shows a pydantic refusal of an empty-looking value in the field's words", async () => {
    backend(secretViews(), {
      "PUT /api/secrets/TELEGRAM_BOT_TOKEN": invalid(
        ["body", "value"],
        "Value error, no control characters",
      ),
    });
    render(<SettingsPage />);
    await userEvent.type(await screen.findByLabelText("Value for TELEGRAM_BOT_TOKEN"), "x");
    await userEvent.click(
      within(screen.getByRole("group", { name: "TELEGRAM_BOT_TOKEN" })).getByRole("button", {
        name: "Save",
      }),
    );
    expect(await screen.findByText("no control characters")).toBeInTheDocument();
  });
});
