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
    expect(within(embeddings).getByLabelText("Endpoint or server model")).toHaveValue("");
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

  it("offers the built-in model, a local server and an endpoint, in that order", async () => {
    settingsBackend();
    render(<SettingsPage />);
    const embeddings = await panel();
    const options = within(within(embeddings).getByLabelText("Embedder")).getAllByRole("option");
    expect(options.map((option) => option.getAttribute("value"))).toEqual([
      "local",
      "server",
      "openai",
    ]);
    expect(options[1]).toHaveTextContent("A local server (LM Studio, Ollama), no key");
  });

  it("shows the address but no key row when a local server is the embedder in force", async () => {
    settingsBackend(
      settingsView({ embeddings: { provider: "server", model: "bge-m3", min_score: null } }),
    );
    render(<SettingsPage />);
    const embeddings = await panel();
    expect(within(embeddings).getByLabelText("Embedder")).toHaveValue("server");
    expect(
      await within(embeddings).findByRole("group", { name: "EMBEDDINGS_BASE_URL" }),
    ).toBeInTheDocument();
    expect(within(embeddings).queryByRole("group", { name: "EMBEDDINGS_API_KEY" })).toBeNull();
    expect(screen.queryByRole("group", { name: "EMBEDDINGS_API_KEY" })).toBeNull();
  });

  it("asks before choosing a local server, saves it with its model and says it re-embeds", async () => {
    const fetchMock = settingsBackend(settingsView(), [json(settingsView())]);
    render(<SettingsPage />);
    const embeddings = await panel();

    await userEvent.selectOptions(within(embeddings).getByLabelText("Embedder"), "server");
    await userEvent.type(within(embeddings).getByLabelText("Endpoint or server model"), "bge-m3");
    await userEvent.click(within(embeddings).getByRole("button", SAVE));
    const confirm = within(embeddings).getByRole("status", { name: "Confirm the change" });
    expect(confirm).toHaveTextContent("embed the memory and the knowledge index again");
    expect(putsOf(fetchMock)).toHaveLength(0);
    await userEvent.click(within(confirm).getByRole("button", { name: "Save for the next start" }));

    expect(sentBody(fetchMock, "PUT")).toEqual({
      embeddings: { provider: "server", model: "bge-m3" },
    });
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

    const model = within(embeddings).getByLabelText("Endpoint or server model");
    await userEvent.clear(model);
    await userEvent.type(model, "bge-m3");
    await userEvent.click(within(embeddings).getByRole("button", SAVE));

    expect(putsOf(fetchMock)).toHaveLength(0);
    const confirm = within(embeddings).getByRole("status", { name: "Confirm the change" });
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
    const confirm = within(embeddings).getByRole("status", { name: "Confirm the change" });
    expect(confirm).toHaveTextContent("embed the memory and the knowledge index again");
    await userEvent.click(within(confirm).getByRole("button", { name: "Cancel" }));

    expect(within(embeddings).queryByRole("status", { name: "Confirm the change" })).toBeNull();
    expect(within(embeddings).getByLabelText("Embedder")).toHaveValue("openai");
    expect(putsOf(fetchMock)).toHaveLength(0);
  });

  it("says a minimum score change applies at the next start but embeds nothing again", async () => {
    const fetchMock = settingsBackend(settingsView(), [json(settingsView())]);
    render(<SettingsPage />);
    const embeddings = await panel();

    await userEvent.type(within(embeddings).getByLabelText("Recall's minimum score"), "0.4");
    await userEvent.click(within(embeddings).getByRole("button", SAVE));
    const confirm = within(embeddings).getByRole("status", { name: "Confirm the change" });
    expect(confirm).toHaveTextContent("applies at the next start");
    expect(confirm).not.toHaveTextContent("embed the memory");
    await userEvent.click(within(confirm).getByRole("button", { name: "Save for the next start" }));

    expect(sentBody(fetchMock, "PUT")).toEqual({ embeddings: { min_score: 0.4 } });
  });

  it("does not warn about a re-embed for a model change while the local model embeds", async () => {
    settingsBackend();
    render(<SettingsPage />);
    const embeddings = await panel();
    await userEvent.type(within(embeddings).getByLabelText("Endpoint or server model"), "x");
    await userEvent.click(within(embeddings).getByRole("button", SAVE));
    expect(
      within(embeddings).getByRole("status", { name: "Confirm the change" }),
    ).not.toHaveTextContent("embed the memory");
  });

  it("drops the question when the draft changes again, so a stale body is never sent", async () => {
    const fetchMock = settingsBackend();
    render(<SettingsPage />);
    const embeddings = await panel();
    await userEvent.type(within(embeddings).getByLabelText("Recall's minimum score"), "0.4");
    await userEvent.click(within(embeddings).getByRole("button", SAVE));
    await userEvent.type(within(embeddings).getByLabelText("Recall's minimum score"), "5");

    expect(within(embeddings).queryByRole("status", { name: "Confirm the change" })).toBeNull();
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
    expect(within(embeddings).getByLabelText("Endpoint or server model")).toBeInTheDocument();
  });

  it("shows the 409 for clearing the address while the endpoint embeds under its row", async () => {
    const refusal =
      "EMBEDDINGS_BASE_URL cannot be cleared while an endpoint or a local server embeds the memory; choose the built-in model, restart Bleep, then clear it";
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

  it("moves focus to the question's first button, names the question for it, and returns to Save on Cancel", async () => {
    settingsBackend();
    render(<SettingsPage />);
    const embeddings = await panel();
    await userEvent.type(within(embeddings).getByLabelText("Recall's minimum score"), "0.4");
    await userEvent.click(within(embeddings).getByRole("button", SAVE));

    const confirm = within(embeddings).getByRole("status", { name: "Confirm the change" });
    const yes = within(confirm).getByRole("button", { name: "Save for the next start" });
    expect(yes).toHaveFocus();
    expect(yes).toHaveAccessibleDescription(/applies at the next start/);

    await userEvent.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(within(embeddings).getByRole("button", SAVE)).toHaveFocus();
  });

  it("does not ask or enable Save for a cosmetic change: 0.30 is 0.3", async () => {
    settingsBackend(
      settingsView({
        embeddings: { provider: "local", model: null, min_score: 0.3 },
        overrides: { embeddings: { min_score: 0.3 } },
      }),
    );
    render(<SettingsPage />);
    const embeddings = await panel();
    const score = within(embeddings).getByLabelText("Recall's minimum score");
    await userEvent.clear(score);
    await userEvent.type(score, "0.30");
    expect(within(embeddings).getByRole("button", SAVE)).toBeDisabled();
    await userEvent.clear(score);
    await userEvent.type(score, "0.31");
    expect(within(embeddings).getByRole("button", SAVE)).toBeEnabled();
  });

  it("puts every field back with Cancel, and a value typed back to the one in force is no change", async () => {
    settingsBackend(
      settingsView({ embeddings: { provider: "local", model: "m", min_score: 0.4 } }),
    );
    render(<SettingsPage />);
    const embeddings = await panel();
    const model = within(embeddings).getByLabelText("Endpoint or server model");

    await userEvent.type(model, "2{Backspace}");
    expect(within(embeddings).getByRole("button", SAVE)).toBeDisabled();
    expect(within(embeddings).queryByRole("button", { name: "Cancel" })).toBeNull();

    await userEvent.selectOptions(within(embeddings).getByLabelText("Embedder"), "openai");
    await userEvent.type(model, "-new");
    await userEvent.click(within(embeddings).getByRole("button", { name: "Cancel" }));
    expect(within(embeddings).getByLabelText("Embedder")).toHaveValue("local");
    expect(model).toHaveValue("m");
    expect(within(embeddings).getByRole("button", SAVE)).toBeDisabled();
  });

  it("treats emptying a field that no override sets as no change", async () => {
    settingsBackend(
      settingsView({ embeddings: { provider: "local", model: "m", min_score: 0.4 } }),
    );
    render(<SettingsPage />);
    const embeddings = await panel();
    await userEvent.clear(within(embeddings).getByLabelText("Endpoint or server model"));
    expect(within(embeddings).getByRole("button", SAVE)).toBeDisabled();
  });

  it("asks before clearing an override by emptying its field, and sends the overrides without it", async () => {
    const fetchMock = settingsBackend(
      settingsView({
        embeddings: { provider: "local", model: null, min_score: 0.4 },
        overrides: { embeddings: { min_score: 0.4 } },
      }),
      [json(settingsView())],
    );
    render(<SettingsPage />);
    const embeddings = await panel();
    await userEvent.clear(within(embeddings).getByLabelText("Recall's minimum score"));
    await userEvent.click(within(embeddings).getByRole("button", SAVE));
    const confirm = within(embeddings).getByRole("status", { name: "Confirm the change" });
    expect(confirm).toHaveTextContent("A minimum score does not embed anything again");
    await userEvent.click(within(confirm).getByRole("button", { name: "Save for the next start" }));

    expect(sentBody(fetchMock, "PUT")).toEqual({ embeddings: {} });
  });

  it("gives Back to the defaults the re-embed question, and sends {} only after it", async () => {
    const fetchMock = settingsBackend(
      settingsView({
        embeddings: endpoint(),
        overrides: { embeddings: { provider: "openai", model: "text-embedding-3-small" } },
      }),
      [json(settingsView())],
    );
    render(<SettingsPage />);
    const embeddings = await panel();
    await userEvent.click(within(embeddings).getByRole("button", { name: "Back to the defaults" }));

    expect(putsOf(fetchMock)).toHaveLength(0);
    const confirm = within(embeddings).getByRole("status", { name: "Confirm the change" });
    expect(confirm).toHaveTextContent("embed the memory and the knowledge index again");
    await userEvent.click(within(confirm).getByRole("button", { name: "Save for the next start" }));
    expect(sentBody(fetchMock, "PUT")).toEqual({ embeddings: {} });
  });

  it("does not submit when Enter is pressed in a field while the question is shown, and saves once on the answer", async () => {
    const fetchMock = settingsBackend(settingsView(), [json(settingsView())]);
    render(<SettingsPage />);
    const embeddings = await panel();
    const score = within(embeddings).getByLabelText("Recall's minimum score");
    await userEvent.type(score, "0.4");
    await userEvent.click(within(embeddings).getByRole("button", SAVE));
    await userEvent.type(score, "{Enter}");

    expect(putsOf(fetchMock)).toHaveLength(0);
    const confirm = within(embeddings).getByRole("status", { name: "Confirm the change" });
    await userEvent.click(within(confirm).getByRole("button", { name: "Save for the next start" }));
    expect(putsOf(fetchMock)).toHaveLength(1);
  });
});
