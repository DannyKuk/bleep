import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ModelSettings } from "@/components/settings/models";

const KEY = { env: "OPENROUTER_API_KEY", file: null, set: false };

describe("ModelSettings", () => {
  it("shows the models in force read-only and sends changes to the dev panel", () => {
    render(
      <ModelSettings
        models={{
          provider: "openrouter",
          model: "openai/gpt-6-luna",
          cheap_model: "openai/gpt-6-mini",
          local_model: "openai/gpt-oss-20b",
        }}
        apiKey={KEY}
      />,
    );
    expect(screen.getByText("OpenRouter")).toBeInTheDocument();
    expect(screen.getByText("openai/gpt-6-luna")).toBeInTheDocument();
    expect(screen.getByText("openai/gpt-6-mini")).toBeInTheDocument();
    expect(screen.getByText("env OPENROUTER_API_KEY")).toBeInTheDocument();
    expect(screen.getByText("Missing:")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.getByRole("link", { name: "dev panel" })).toHaveAttribute("href", "/dev");
  });

  it("shows the local model for the local provider, which needs no key", () => {
    render(
      <ModelSettings
        models={{
          provider: "local",
          model: "openai/gpt-6-luna",
          cheap_model: "openai/gpt-6-luna",
          local_model: "openai/gpt-oss-20b",
        }}
        apiKey={KEY}
      />,
    );
    expect(screen.getByText("openai/gpt-oss-20b")).toBeInTheDocument();
    expect(screen.queryByText("openai/gpt-6-luna")).toBeNull();
    expect(screen.getByText("(not needed locally)")).toBeInTheDocument();
  });
});
