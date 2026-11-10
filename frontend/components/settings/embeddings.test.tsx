import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  json,
  secretViews,
  sentBody,
  settingsBackend,
  settingsView,
} from "@/components/settings/fixtures";
import { SettingsPage } from "@/components/settings/settings-page";

afterEach(() => vi.unstubAllGlobals());

const panel = () => screen.findByRole("region", { name: "Embeddings" });
const putsOf = (fetchMock: ReturnType<typeof settingsBackend>) =>
  fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT");
const endpoint = (over = {}) => ({
  provider: "openai" as const,
  model: "text-embedding-3-small",
  min_score: null,
  ...over,
});
const SAVE = { name: "Save the embeddings" };

describe("the Embeddings panel", () => {
  it("shows the embedder in force and an empty minimum score for the model's own", async () => {
    settingsBackend();
    render(<SettingsPage />);
    const embeddings = await panel();

    expect(within(embeddings).getByLabelText("Embedder")).toHaveValue("local");
    expect(within(embeddings).getByLabelText("Endpoint model")).toHaveValue("");
    expect(within(embeddings).getByLabelText("Recall's minimum score")).toHaveValue("");
    expect(within(embeddings).queryByRole("status")).toBeNull();
  });

  it("shows EMBEDDINGS_API_KEY only when the embedder in force is an endpoint", async () => {
    settingsBackend();
    const local = render(<SettingsPage />);
    const embeddings = await panel();
    expect(
      within(embeddings).getByRole("group", { name: "EMBEDDINGS_BASE_URL" }),
    ).toBeInTheDocument();
    expect(within(embeddings).queryByRole("group", { name: "EMBEDDINGS_API_KEY" })).toBeNull();
    expect(screen.queryByRole("group", { name: "EMBEDDINGS_API_KEY" })).toBeNull();
    local.unmount();

    settingsBackend(settingsView({ embeddings: endpoint() }));
    render(<SettingsPage />);
    const withEndpoint = await panel();
    expect(
      await within(withEndpoint).findByRole("group", { name: "EMBEDDINGS_API_KEY" }),
    ).toBeInTheDocument();
    const secrets = screen.getByRole("region", { name: "Secrets" });
    expect(within(secrets).queryByRole("group", { name: "EMBEDDINGS_API_KEY" })).toBeNull();
  });

  it("asks before saving a model change and says it re-embeds at the next start", async () => {
    const fetchMock = settingsBackend(
      settingsView({
        embeddings: endpoint(),
        overrides: { embeddings: { provider: "openai", model: "text-embedding-3-small" } },
      }),
      [json(settingsView({ embeddings: endpoint({ model: "bge-m3" }) }))],
    );
    render(<SettingsPage />);
    const embeddings = await panel();

    const model = within(embeddings).getByLabelText("Endpoint model");
    await userEvent.clear(model);
    await userEvent.type(model, "bge-m3");
    await userEvent.click(within(embeddings).getByRole("button", SAVE));

    expect(putsOf(fetchMock)).toHaveLength(0);
    const confirm = within(embeddings).getByRole("group", { name: "Confirm the change" });
    expect(confirm).toHaveTextContent("applies at the next start");
    expect(confirm).toHaveTextContent("embed the memory and the knowledge index again");

    await userEvent.click(within(confirm).getByRole("button", { name: "Save for the next start" }));
    expect(sentBody(fetchMock, "PUT")).toEqual({
      embeddings: { provider: "openai", model: "bge-m3" },
    });
  });

  it("asks before a provider change too, and Cancel sends nothing and keeps the draft", async () => {
    const fetchMock = settingsBackend();
    render(<SettingsPage />);
    const embeddings = await panel();

    await userEvent.selectOptions(within(embeddings).getByLabelText("Embedder"), "openai");
    await userEvent.click(within(embeddings).getByRole("button", SAVE));
    const confirm = within(embeddings).getByRole("group", { name: "Confirm the change" });
    expect(confirm).toHaveTextContent("embed the memory and the knowledge index again");
    await userEvent.click(within(confirm).getByRole("button", { name: "Cancel" }));

    expect(within(embeddings).queryByRole("group", { name: "Confirm the change" })).toBeNull();
    expect(within(embeddings).getByLabelText("Embedder")).toHaveValue("openai");
    expect(putsOf(fetchMock)).toHaveLength(0);
  });

  it("says a minimum score change applies at the next start but embeds nothing again", async () => {
    const fetchMock = settingsBackend(settingsView(), [json(settingsView())]);
    render(<SettingsPage />);
    const embeddings = await panel();

    await userEvent.type(within(embeddings).getByLabelText("Recall's minimum score"), "0.4");
    await userEvent.click(within(embeddings).getByRole("button", SAVE));
    const confirm = within(embeddings).getByRole("group", { name: "Confirm the change" });
    expect(confirm).toHaveTextContent("applies at the next start");
    expect(confirm).not.toHaveTextContent("embed the memory");
    await userEvent.click(within(confirm).getByRole("button", { name: "Save for the next start" }));

    expect(sentBody(fetchMock, "PUT")).toEqual({ embeddings: { min_score: 0.4 } });
  });

  it("does not warn about a re-embed for a model change while the local model embeds", async () => {
    settingsBackend();
    render(<SettingsPage />);
    const embeddings = await panel();
    await userEvent.type(within(embeddings).getByLabelText("Endpoint model"), "x");
    await userEvent.click(within(embeddings).getByRole("button", SAVE));
    expect(
      within(embeddings).getByRole("group", { name: "Confirm the change" }),
    ).not.toHaveTextContent("embed the memory");
  });

  it("drops the question when the draft changes again, so a stale body is never sent", async () => {
    const fetchMock = settingsBackend();
    render(<SettingsPage />);
    const embeddings = await panel();
    await userEvent.type(within(embeddings).getByLabelText("Recall's minimum score"), "0.4");
    await userEvent.click(within(embeddings).getByRole("button", SAVE));
    await userEvent.type(within(embeddings).getByLabelText("Recall's minimum score"), "5");

    expect(within(embeddings).queryByRole("group", { name: "Confirm the change" })).toBeNull();
    expect(putsOf(fetchMock)).toHaveLength(0);
  });

  it("shows the API's refusal under the form after the confirm", async () => {
    settingsBackend(settingsView(), [
      json({ detail: "set EMBEDDINGS_BASE_URL first: an endpoint needs its address" }, 422),
    ]);
    render(<SettingsPage />);
    const embeddings = await panel();
    await userEvent.selectOptions(within(embeddings).getByLabelText("Embedder"), "openai");
    await userEvent.click(within(embeddings).getByRole("button", SAVE));
    await userEvent.click(
      within(embeddings).getByRole("button", { name: "Save for the next start" }),
    );

    expect(await within(embeddings).findByRole("alert")).toHaveTextContent(
      "set EMBEDDINGS_BASE_URL first",
    );
    expect(within(embeddings).getByLabelText("Embedder")).toHaveValue("openai");
  });

  it("says a restart is needed while the API lists embeddings, and says nothing otherwise", async () => {
    settingsBackend(settingsView({ restart_needed: ["embeddings"] }));
    render(<SettingsPage />);
    const embeddings = await panel();
    expect(within(embeddings).getByRole("status")).toHaveTextContent("A restart is needed");
    expect(
      within(screen.getByRole("region", { name: "Tracing" })).queryByRole("status"),
    ).toBeNull();
  });

  it("shows a field the env sets as set there, with no input", async () => {
    settingsBackend(
      settingsView({
        embeddings: endpoint({ min_score: 0.6 }),
        from_env: ["embeddings.provider", "embeddings.min_score"],
      }),
    );
    render(<SettingsPage />);
    const embeddings = await panel();

    const score = within(embeddings).getByRole("group", { name: "Recall's minimum score" });
    expect(score).toHaveTextContent("Set in the environment (.env or Compose)");
    expect(score).toHaveTextContent("0.6");
    expect(within(score).queryByRole("textbox")).toBeNull();
    expect(within(embeddings).getByRole("group", { name: "Embedder" })).toHaveTextContent(
      "EMBEDDINGS_PROVIDER",
    );
    expect(within(embeddings).getByLabelText("Endpoint model")).toBeInTheDocument();
  });

  it("shows the 409 for clearing the address while the endpoint embeds under its row", async () => {
    const refusal =
      "EMBEDDINGS_BASE_URL cannot be cleared while the endpoint embeds the memory; choose the local model, restart Bleep, then clear it";
    const views = secretViews().map((view) =>
      view.name === "EMBEDDINGS_BASE_URL"
        ? {
            ...view,
            set: true,
            source: "dashboard" as const,
            value: "http://lan:1234",
            version: "a1",
          }
        : view,
    );
    const fetchMock = settingsBackend(settingsView({ embeddings: endpoint() }), [], views);
    const base = fetchMock.getMockImplementation() as typeof fetch;
    fetchMock.mockImplementation(async (input, init) =>
      String(input) === "/api/secrets/EMBEDDINGS_BASE_URL" && init?.method === "DELETE"
        ? json({ detail: refusal }, 409)
        : base(input, init),
    );
    render(<SettingsPage />);
    const embeddings = await panel();
    const row = await within(embeddings).findByRole("group", { name: "EMBEDDINGS_BASE_URL" });

    await userEvent.click(within(row).getByRole("button", { name: "Clear" }));

    expect(await within(row).findByRole("alert")).toHaveTextContent(refusal);
  });
});
