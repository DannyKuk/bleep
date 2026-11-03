import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { json, MODELS, sentBody, settingsView, stubBackend } from "@/components/dev/fixtures";
import { ModelsForm } from "@/components/dev/models";

describe("ModelsForm", () => {
  it("saves only what changed, on top of the overrides already stored", async () => {
    const saved = settingsView({
      models: { ...MODELS, provider: "local", model: "openai/gpt-6" },
      overrides: { models: { provider: "local", model: "openai/gpt-6" } },
    });
    const fetchMock = stubBackend(json(saved));
    const onSaved = vi.fn();
    render(
      <ModelsForm
        view={settingsView({
          overrides: { models: { provider: "local" } },
          models: { ...MODELS, provider: "local" },
        })}
        onSaved={onSaved}
      />,
    );
    const save = screen.getByRole("button", { name: "Save the models" });
    expect(save).toBeDisabled();

    const model = screen.getByLabelText("Model");
    await userEvent.clear(model);
    await userEvent.type(model, "openai/gpt-6 ");
    await userEvent.click(save);

    expect(fetchMock.mock.calls[0][0]).toBe("/api/settings");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "PUT" });
    expect(sentBody(fetchMock, 0)).toEqual({
      models: { provider: "local", model: "openai/gpt-6" },
    });
    expect(onSaved).toHaveBeenCalledWith(saved);
  });

  it("clears the overrides to bring back the env's models", async () => {
    const fetchMock = stubBackend(json(settingsView()));
    render(
      <ModelsForm
        view={settingsView({ overrides: { models: { model: "x/y" } } })}
        onSaved={vi.fn()}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Use the env's models" }));

    expect(sentBody(fetchMock, 0)).toEqual({ models: {} });
  });

  it("offers no reset without an override, and says why a save failed", async () => {
    stubBackend(
      json({ detail: [{ loc: ["models", "model"], msg: "String should match pattern" }] }, 422),
    );
    render(<ModelsForm view={settingsView()} onSaved={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Use the env's models" })).not.toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Provider"), "local");
    await userEvent.click(screen.getByRole("button", { name: "Save the models" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      'Could not save the models: [{"loc":["models","model"]',
    );
  });
});
