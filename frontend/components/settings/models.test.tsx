import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  DEFAULT_IDS,
  idsWith,
  invalid,
  json,
  MODEL_EXAMPLES,
  modelsView,
  sentBody,
  settingsBackend,
  settingsView,
} from "@/components/settings/fixtures";
import { ModelSettings } from "@/components/settings/models";
import { SettingsPage } from "@/components/settings/settings-page";
import { MESSAGES } from "@/lib/i18n";

afterEach(() => vi.unstubAllGlobals());

const panel = () => screen.findByRole("region", { name: "Models" });
const putsOf = (fetchMock: ReturnType<typeof settingsBackend>) =>
  fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT");
const saveModels = (models: HTMLElement) =>
  userEvent.click(within(models).getByRole("button", { name: "Save the models" }));

describe("the Models panel", () => {
  it("shows the models in force as inputs, with the key and the local address as rows", async () => {
    settingsBackend();
    render(<SettingsPage />);
    const models = await panel();

    expect(within(models).getByLabelText("Provider")).toHaveValue("openrouter");
    expect(
      within(within(models).getByLabelText("Provider")).getAllByRole("option", { hidden: true }),
    ).toHaveLength(3);
    expect(within(models).getByRole("option", { name: "Anthropic" })).toBeInTheDocument();
    expect(within(models).getByLabelText("Model")).toHaveValue("openai/gpt-6-luna");
    expect(within(models).getByLabelText("Cheap model")).toHaveValue("openai/gpt-6-luna");
    expect(within(models).queryByLabelText("Local model")).toBeNull();
    expect(within(models).getByRole("group", { name: "OPENROUTER_API_KEY" })).toHaveTextContent(
      "Set in .env",
    );
    expect(within(models).queryByRole("group", { name: "ANTHROPIC_API_KEY" })).toBeNull();
    expect(within(models).queryByRole("group", { name: "LOCAL_BASE_URL" })).toBeNull();
    expect(within(models).getByRole("button", { name: "Save the models" })).toBeDisabled();
    expect(within(models).queryByRole("status")).toBeNull();
  });

  it("sends only what changed on top of the overrides, with the version it read", async () => {
    const saved = {
      ...modelsView({
        saved: "anthropic",
        ids: idsWith({ model: "x/y" }),
        stored: { provider: "anthropic", model: "x/y" },
      }),
      version: "v2",
    };
    const fetchMock = settingsBackend(
      modelsView({ saved: "anthropic", stored: { provider: "anthropic" } }),
      [json(saved)],
    );
    render(<SettingsPage />);
    const models = await panel();

    const model = within(models).getByLabelText("Model");
    await userEvent.clear(model);
    await userEvent.type(model, "x/y ");
    await userEvent.click(within(models).getByRole("button", { name: "Save the models" }));

    expect(sentBody(fetchMock, "PUT")).toEqual({ models: { provider: "anthropic", model: "x/y" } });
    expect(putsOf(fetchMock)[0][1]?.headers).toMatchObject({ "if-match": '"v1"' });
    expect(await within(models).findByDisplayValue("x/y")).toBeInTheDocument();
  });

  it("leaves an emptied field out, so the layer below shows", async () => {
    const fetchMock = settingsBackend(
      modelsView({
        ids: idsWith({ model: "x/y", cheap_model: "x/z" }),
        stored: { model: "x/y", cheap_model: "x/z" },
      }),
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
    await panel();
    const secrets = await screen.findByRole("region", { name: "Secrets" });

    expect(within(secrets).queryByRole("group", { name: "OPENROUTER_API_KEY" })).toBeNull();
    expect(within(secrets).queryByRole("group", { name: "ANTHROPIC_API_KEY" })).toBeNull();
    expect(within(secrets).queryByRole("group", { name: "LOCAL_BASE_URL" })).toBeNull();
    expect(within(secrets).getByRole("group", { name: "TELEGRAM_BOT_TOKEN" })).toBeInTheDocument();
  });

  it("shows only the fields and the key or address of the provider in the select", async () => {
    settingsBackend();
    render(<SettingsPage />);
    const models = await panel();
    const provider = within(models).getByLabelText("Provider");

    await userEvent.selectOptions(provider, "anthropic");
    expect(within(models).getByRole("group", { name: "ANTHROPIC_API_KEY" })).toBeInTheDocument();
    expect(within(models).queryByRole("group", { name: "OPENROUTER_API_KEY" })).toBeNull();
    expect(within(models).queryByLabelText("Local model")).toBeNull();

    await userEvent.selectOptions(provider, "local");
    expect(within(models).getByLabelText("Local model")).toHaveValue("openai/gpt-oss-20b");
    expect(within(models).getByRole("group", { name: "LOCAL_BASE_URL" })).toBeInTheDocument();
    expect(within(models).queryByRole("group", { name: "ANTHROPIC_API_KEY" })).toBeNull();
    expect(within(models).queryByLabelText("Model")).toBeNull();
    expect(within(models).queryByLabelText("Cheap model")).toBeNull();
  });

  it("neither sends nor clears the stored value of a hidden field", async () => {
    const fetchMock = settingsBackend(
      modelsView({
        ids: idsWith({ model: "x/y", local_model: "my/local" }),
        stored: { model: "x/y", local_model: "my/local" },
      }),
      [json(settingsView())],
    );
    render(<SettingsPage />);
    const models = await panel();

    await userEvent.clear(within(models).getByLabelText("Model"));
    await userEvent.selectOptions(within(models).getByLabelText("Provider"), "local");
    expect(within(models).queryByLabelText("Model")).toBeNull();
    await saveModels(models);

    expect(sentBody(fetchMock, "PUT")).toEqual({
      models: { model: "x/y", local_model: "my/local", provider: "local" },
    });
  });

  it("offers the example ids of the provider in the select, labelled as examples", async () => {
    settingsBackend();
    render(<SettingsPage />);
    const models = await panel();
    const examplesOf = (label: string) => {
      const input = within(models).getByLabelText(label);
      const list = document.getElementById(input.getAttribute("list") ?? "");
      return [...(list?.querySelectorAll("option") ?? [])].map((option) => option.value);
    };

    expect(examplesOf("Model")).toEqual(MODEL_EXAMPLES.openrouter);
    expect(within(models).getByLabelText("Model")).toHaveAccessibleDescription(
      "Examples: openai/gpt-6-luna, openai/gpt-6.1-terra, openai/gpt-6.1-sol. Any other id works too.",
    );
    await userEvent.selectOptions(within(models).getByLabelText("Provider"), "anthropic");
    expect(examplesOf("Model")).toEqual(MODEL_EXAMPLES.anthropic);
    expect(examplesOf("Cheap model")).toEqual(MODEL_EXAMPLES.anthropic);
    expect(within(models).getByLabelText("Cheap model")).toHaveAccessibleDescription(
      "Writes the one-line recovery explanations. Examples: claude-haiku-5-5, claude-sonnet-5-5, claude-opus-5-5. Any other id works too.",
    );
    await userEvent.selectOptions(within(models).getByLabelText("Provider"), "local");
    expect(examplesOf("Local model")).toEqual(MODEL_EXAMPLES.local);
  });

  it("keeps an id typed before a switch under every provider, and saves it", async () => {
    const fetchMock = settingsBackend();
    render(<SettingsPage />);
    const models = await panel();
    const provider = within(models).getByLabelText("Provider");

    const model = within(models).getByLabelText("Model");
    await userEvent.clear(model);
    await userEvent.type(model, "claude-opus-5-5");
    await userEvent.selectOptions(provider, "local");
    await userEvent.selectOptions(provider, "anthropic");
    expect(within(models).getByLabelText("Model")).toHaveValue("claude-opus-5-5");
    expect(within(models).getByLabelText("Cheap model")).toHaveValue("claude-haiku-5-5");
    await userEvent.selectOptions(provider, "openrouter");
    expect(within(models).getByLabelText("Model")).toHaveValue("claude-opus-5-5");
    expect(within(models).getByLabelText("Cheap model")).toHaveValue("openai/gpt-6-luna");
    await userEvent.selectOptions(provider, "anthropic");
    await saveModels(models);

    expect(sentBody(fetchMock, "PUT")).toEqual({
      models: { provider: "anthropic", model: "claude-opus-5-5" },
    });
  });

  it("counts text typed back to what the field shows as not edited, so it follows the provider", async () => {
    const fetchMock = settingsBackend(modelsView({ saved: "anthropic", ids: DEFAULT_IDS }));
    render(<SettingsPage />);
    const models = await panel();
    const model = within(models).getByLabelText("Model");

    await userEvent.type(model, "x{Backspace}");
    await userEvent.clear(within(models).getByLabelText("Cheap model"));
    await userEvent.type(within(models).getByLabelText("Cheap model"), "claude-haiku-5-5");
    expect(within(models).getByRole("button", { name: "Save the models" })).toBeDisabled();

    await userEvent.selectOptions(within(models).getByLabelText("Provider"), "openrouter");
    expect(within(models).getByLabelText("Model")).toHaveValue("openai/gpt-6-luna");
    expect(within(models).getByLabelText("Cheap model")).toHaveValue("openai/gpt-6-luna");
    await saveModels(models);

    expect(sentBody(fetchMock, "PUT")).toEqual({ models: { provider: "openrouter" } });
  });

  it("never changes a field under the cursor while an id is typed into it", async () => {
    const fetchMock = settingsBackend(modelsView({ saved: "anthropic", ids: DEFAULT_IDS }));
    render(<SettingsPage />);
    const models = await panel();

    await userEvent.selectOptions(within(models).getByLabelText("Provider"), "openrouter");
    const cheap = within(models).getByLabelText("Cheap model");
    await userEvent.clear(cheap);
    for (const key of "claude-haiku-5-5") {
      const before = (cheap as HTMLInputElement).value;
      await userEvent.type(cheap, key);
      expect(cheap).toHaveValue(before + key);
    }
    await saveModels(models);

    expect(sentBody(fetchMock, "PUT")).toEqual({
      models: { provider: "openrouter", cheap_model: "claude-haiku-5-5" },
    });
  });

  it("saves a typed id that a later pick's own id happens to equal", async () => {
    const fetchMock = settingsBackend();
    render(<SettingsPage />);
    const models = await panel();

    const model = within(models).getByLabelText("Model");
    await userEvent.clear(model);
    await userEvent.type(model, "claude-haiku-5-5");
    await userEvent.selectOptions(within(models).getByLabelText("Provider"), "anthropic");
    await saveModels(models);

    expect(sentBody(fetchMock, "PUT")).toEqual({
      models: { provider: "anthropic", model: "claude-haiku-5-5" },
    });
  });

  it("saves a pasted id with a trailing space, trimmed, when a later pick runs the same id", async () => {
    const fetchMock = settingsBackend();
    render(<SettingsPage />);
    const models = await panel();

    const model = within(models).getByLabelText("Model");
    await userEvent.clear(model);
    await userEvent.click(model);
    await userEvent.paste("claude-haiku-5-5 ");
    await userEvent.selectOptions(within(models).getByLabelText("Provider"), "anthropic");
    expect(within(models).getByLabelText("Model")).toHaveValue("claude-haiku-5-5 ");
    await saveModels(models);

    expect(sentBody(fetchMock, "PUT")).toEqual({
      models: { provider: "anthropic", model: "claude-haiku-5-5" },
    });
  });

  it("keeps Save and Cancel for a typed id after switching back to the saved provider", async () => {
    const fetchMock = settingsBackend();
    render(<SettingsPage />);
    const models = await panel();
    const provider = within(models).getByLabelText("Provider");

    await userEvent.selectOptions(provider, "anthropic");
    const model = within(models).getByLabelText("Model");
    await userEvent.clear(model);
    await userEvent.type(model, "openai/gpt-6-luna");
    await userEvent.selectOptions(provider, "openrouter");
    expect(within(models).getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    await userEvent.selectOptions(provider, "anthropic");
    expect(within(models).getByLabelText("Model")).toHaveValue("openai/gpt-6-luna");
    await userEvent.selectOptions(provider, "openrouter");
    await saveModels(models);

    expect(sentBody(fetchMock, "PUT")).toEqual({ models: { model: "openai/gpt-6-luna" } });
  });

  it("puts the provider, the fields and the key row back with Cancel", async () => {
    settingsBackend();
    render(<SettingsPage />);
    const models = await panel();

    await userEvent.type(within(models).getByLabelText("Model"), "-mine");
    await userEvent.selectOptions(within(models).getByLabelText("Provider"), "anthropic");
    expect(within(models).getByRole("group", { name: "ANTHROPIC_API_KEY" })).toBeInTheDocument();
    await userEvent.click(within(models).getByRole("button", { name: "Cancel" }));

    expect(within(models).getByLabelText("Provider")).toHaveValue("openrouter");
    expect(within(models).getByLabelText("Model")).toHaveValue("openai/gpt-6-luna");
    expect(within(models).getByRole("button", { name: "Save the models" })).toBeDisabled();
    expect(within(models).getByRole("group", { name: "OPENROUTER_API_KEY" })).toBeInTheDocument();
    expect(within(models).queryByRole("group", { name: "ANTHROPIC_API_KEY" })).toBeNull();
    await userEvent.selectOptions(within(models).getByLabelText("Provider"), "anthropic");
    expect(within(models).getByLabelText("Model")).toHaveValue("claude-haiku-5-5");
  });

  it("no longer sends anyone to the dev panel for the models", async () => {
    settingsBackend();
    render(<SettingsPage />);
    await panel();
    expect(screen.queryByRole("link", { name: "dev panel" })).toBeNull();
  });
});

const PROVIDERS = ["openrouter", "anthropic", "local"] as const;
type Provider = (typeof PROVIDERS)[number];
const LABELS = { model: "Model", cheap_model: "Cheap model", local_model: "Local model" } as const;

/** What a person may have stored or set, with the ids the API gives for it per provider. */
const STATES: {
  name: string;
  stored: Record<string, string>;
  env: boolean;
  ids: typeof DEFAULT_IDS;
}[] = [
  { name: "nothing stored", stored: {}, env: false, ids: DEFAULT_IDS },
  {
    name: "an override equal to the OpenRouter default",
    stored: { model: "openai/gpt-6-luna" },
    env: false,
    ids: DEFAULT_IDS,
  },
  {
    name: "a custom override",
    stored: { model: "x/mine", local_model: "my/local" },
    env: false,
    ids: idsWith({ model: "x/mine", local_model: "my/local" }),
  },
  { name: "an env model equal to the OpenRouter default", stored: {}, env: true, ids: DEFAULT_IDS },
  {
    name: "a custom env model",
    stored: {},
    env: true,
    ids: idsWith({ model: "openai/env-model" }),
  },
];

const SEQUENCES: Provider[][] = PROVIDERS.flatMap((a) =>
  PROVIDERS.flatMap((b) => PROVIDERS.map((c) => [a, b, c])),
);

function expectShows(form: HTMLElement, state: (typeof STATES)[number], provider: Provider) {
  const shown = provider === "local" ? ["local_model"] : ["model", "cheap_model"];
  for (const [name, label] of Object.entries(LABELS) as [keyof typeof LABELS, string][]) {
    const want = state.ids[provider][name];
    if (!shown.includes(name)) {
      expect(within(form).queryByLabelText(label)).toBeNull();
      expect(within(form).queryByRole("group", { name: label })).toBeNull();
    } else if (state.env && name === "model") {
      const group = within(form).getByRole("group", { name: label });
      expect(group).toHaveTextContent(want);
      expect(within(group).queryByRole("textbox")).toBeNull();
    } else {
      expect(within(form).getByLabelText(label)).toHaveValue(want);
    }
  }
}

describe.each(STATES)("the Models panel with $name", (state) => {
  it.each(PROVIDERS)(
    "saved %s: every three picks of the provider show the API's ids for the pick, and save only the provider on top of what is stored",
    async (saved) => {
      const stored = saved === "openrouter" ? state.stored : { provider: saved, ...state.stored };
      const view = modelsView({
        saved,
        ids: state.ids,
        stored,
        fromEnv: state.env ? ["models.model"] : [],
      });
      for (const picks of SEQUENCES) {
        const onSave = vi.fn(async () => undefined);
        const { container, unmount } = render(<ModelSettings view={view} onSave={onSave} />);
        expectShows(container, state, saved);
        for (const pick of picks) {
          fireEvent.change(within(container).getByLabelText("Provider"), {
            target: { value: pick },
          });
          expectShows(container, state, pick);
        }
        const last = picks[picks.length - 1];
        const save = within(container).getByRole("button", { name: "Save the models" });
        if (last === saved) {
          expect(save).toBeDisabled();
        } else {
          fireEvent.click(save);
          await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
          expect(onSave).toHaveBeenCalledWith({ models: { ...stored, provider: last } });
        }
        unmount();
      }
    },
  );
});
