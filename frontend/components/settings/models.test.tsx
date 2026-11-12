import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  invalid,
  json,
  sentBody,
  settingsBackend,
  settingsView,
} from "@/components/settings/fixtures";
import { SettingsPage } from "@/components/settings/settings-page";
import { MESSAGES } from "@/lib/i18n";

afterEach(() => vi.unstubAllGlobals());

const panel = () => screen.findByRole("region", { name: "Models" });
const putsOf = (fetchMock: ReturnType<typeof settingsBackend>) =>
  fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT");

describe("the Models panel", () => {
  it("shows the models in force as inputs, with the key and the local address as rows", async () => {
    settingsBackend();
    render(<SettingsPage />);
    const models = await panel();

    expect(within(models).getByLabelText("Provider")).toHaveValue("openrouter");
    expect(within(models).getByLabelText("Model")).toHaveValue("openai/gpt-6-luna");
    expect(within(models).getByLabelText("Cheap model")).toHaveValue("openai/gpt-6-luna");
    expect(within(models).getByLabelText("Local model")).toHaveValue("openai/gpt-oss-20b");
    expect(within(models).getByRole("group", { name: "OPENROUTER_API_KEY" })).toHaveTextContent(
      "Set in .env",
    );
    expect(within(models).getByRole("group", { name: "ANTHROPIC_API_KEY" })).toHaveTextContent(
      "Missing",
    );
    expect(within(models).getByRole("group", { name: "LOCAL_BASE_URL" })).toHaveTextContent(
      "may be private",
    );
    expect(within(models).getByRole("button", { name: "Save the models" })).toBeDisabled();
    expect(within(models).queryByRole("status")).toBeNull();
  });

  it("sends only what changed on top of the overrides, with the version it read", async () => {
    const saved = settingsView({
      version: "v2",
      models: { ...settingsView().models, provider: "local", model: "x/y" },
      overrides: { models: { provider: "local", model: "x/y" } },
    });
    const fetchMock = settingsBackend(
      settingsView({ overrides: { models: { provider: "local" } } }),
      [json(saved)],
    );
    render(<SettingsPage />);
    const models = await panel();

    const model = within(models).getByLabelText("Model");
    await userEvent.clear(model);
    await userEvent.type(model, "x/y ");
    await userEvent.click(within(models).getByRole("button", { name: "Save the models" }));

    expect(sentBody(fetchMock, "PUT")).toEqual({ models: { provider: "local", model: "x/y" } });
    expect(putsOf(fetchMock)[0][1]?.headers).toMatchObject({ "if-match": '"v1"' });
    expect(await within(models).findByDisplayValue("x/y")).toBeInTheDocument();
  });

  it("leaves an emptied field out, so the layer below shows", async () => {
    const fetchMock = settingsBackend(
      settingsView({ overrides: { models: { model: "x/y", cheap_model: "x/z" } } }),
      [json(settingsView())],
    );
    render(<SettingsPage />);
    const models = await panel();

    await userEvent.clear(within(models).getByLabelText("Cheap model"));
    await userEvent.click(within(models).getByRole("button", { name: "Save the models" }));

    expect(sentBody(fetchMock, "PUT")).toEqual({ models: { model: "x/y" } });
  });

  it("clears every override with Back to the defaults", async () => {
    const fetchMock = settingsBackend(settingsView({ overrides: { models: { model: "x/y" } } }), [
      json(settingsView()),
    ]);
    render(<SettingsPage />);
    const models = await panel();
    await userEvent.click(within(models).getByRole("button", { name: "Back to the defaults" }));
    expect(sentBody(fetchMock, "PUT")).toEqual({ models: {} });
  });

  it("shows a field the env sets as set there, with no input", async () => {
    settingsBackend(settingsView({ from_env: ["models.model", "models.provider"] }));
    render(<SettingsPage />);
    const models = await panel();

    for (const label of ["Provider", "Model"]) {
      const field = within(models).getByRole("group", { name: label });
      expect(field).toHaveTextContent("Set in the environment (.env or Compose)");
      expect(within(field).queryByRole("textbox")).toBeNull();
      expect(within(field).queryByRole("combobox")).toBeNull();
    }
    const model = within(models).getByRole("group", { name: "Model" });
    expect(model).toHaveTextContent("openai/gpt-6-luna");
    expect(model).toHaveTextContent("LLM_MODEL");
    expect(within(models).getByLabelText("Cheap model")).toBeInTheDocument();
  });

  it("keeps the draft and shows the API's 422 under the form, named as the form names the field", async () => {
    settingsBackend(settingsView(), [
      invalid(["models", "model"], "String should match pattern '^[^\\s]+$'"),
    ]);
    render(<SettingsPage />);
    const models = await panel();

    await userEvent.type(within(models).getByLabelText("Model"), " bad");
    await userEvent.click(within(models).getByRole("button", { name: "Save the models" }));

    expect(await within(models).findByRole("alert")).toHaveTextContent(
      "Model: String should match pattern",
    );
    expect(within(models).getByLabelText("Model")).toHaveValue("openai/gpt-6-luna bad");
  });

  it("shows Bleep's own sentence for a change the env refuses", async () => {
    const refusal =
      "LLM_MODEL is set in the environment (.env or Compose), which wins; change it there";
    settingsBackend(settingsView(), [json({ detail: refusal }, 422)]);
    render(<SettingsPage />);
    const models = await panel();
    await userEvent.type(within(models).getByLabelText("Model"), "2");
    await userEvent.click(within(models).getByRole("button", { name: "Save the models" }));
    expect(await within(models).findByRole("alert")).toHaveTextContent(refusal);
  });

  it("after a 409 reads the settings again, says so and never retries", async () => {
    const fetchMock = settingsBackend(settingsView(), [
      json({ detail: "changed", version: "v2" }, 409),
    ]);
    render(<SettingsPage />);
    const models = await panel();
    await userEvent.type(within(models).getByLabelText("Model"), "2");
    await userEvent.click(within(models).getByRole("button", { name: "Save the models" }));

    expect((await screen.findAllByRole("alert"))[0]).toHaveTextContent(
      MESSAGES.en.settings.changedElsewhere,
    );
    expect(putsOf(fetchMock)).toHaveLength(1);
  });

  it("lists the model's key and address here and not again under Secrets", async () => {
    settingsBackend();
    render(<SettingsPage />);
    const models = await panel();
    const secrets = await screen.findByRole("region", { name: "Secrets" });

    expect(within(models).getByRole("group", { name: "LOCAL_BASE_URL" })).toBeInTheDocument();
    expect(within(secrets).queryByRole("group", { name: "OPENROUTER_API_KEY" })).toBeNull();
    expect(within(secrets).queryByRole("group", { name: "ANTHROPIC_API_KEY" })).toBeNull();
    expect(within(secrets).queryByRole("group", { name: "LOCAL_BASE_URL" })).toBeNull();
    expect(within(secrets).getByRole("group", { name: "TELEGRAM_BOT_TOKEN" })).toBeInTheDocument();
  });

  it("no longer sends anyone to the dev panel for the models", async () => {
    settingsBackend();
    render(<SettingsPage />);
    await panel();
    expect(screen.queryByRole("link", { name: "dev panel" })).toBeNull();
  });
});
