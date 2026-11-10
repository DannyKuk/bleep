import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  json,
  plugin,
  secretViews,
  sentBody,
  settingsBackend,
  settingsView,
  stubBackend,
  telegram,
} from "@/components/settings/fixtures";
import { SettingsPage } from "@/components/settings/settings-page";

afterEach(() => vi.unstubAllGlobals());

const panel = () => screen.findByRole("region", { name: "Tracing" });
const SAVE = { name: "Save tracing" };

describe("the Tracing panel", () => {
  it("shows the tracer in force, its addresses and projects, and the three keys beside them", async () => {
    settingsBackend();
    render(<SettingsPage />);
    const tracing = await panel();

    expect(within(tracing).getByLabelText("Trace incident runs with")).toHaveValue("off");
    expect(within(tracing).getByLabelText("Base URL")).toHaveValue("http://langfuse-web:3000");
    expect(within(tracing).getByLabelText("UI URL")).toHaveValue("http://localhost:3001");
    expect(within(tracing).getByLabelText("Project id")).toHaveValue("");
    expect(within(tracing).getByLabelText("Endpoint")).toHaveValue("");
    expect(within(tracing).getByLabelText("Project")).toHaveValue("bleep");
    for (const key of ["LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGSMITH_API_KEY"]) {
      expect(within(tracing).getByRole("group", { name: key })).toBeInTheDocument();
    }
    expect(within(tracing).queryByRole("status")).toBeNull();
    const secrets = screen.getByRole("region", { name: "Secrets" });
    expect(within(secrets).queryByRole("group", { name: "LANGSMITH_API_KEY" })).toBeNull();
  });

  it("switches tracing on with no question asked, since it applies at once", async () => {
    const fetchMock = settingsBackend(settingsView(), [json(settingsView())]);
    render(<SettingsPage />);
    const tracing = await panel();

    await userEvent.selectOptions(
      within(tracing).getByLabelText("Trace incident runs with"),
      "langsmith",
    );
    await userEvent.click(within(tracing).getByRole("button", SAVE));

    expect(within(tracing).queryByRole("status", { name: "Confirm the change" })).toBeNull();
    expect(sentBody(fetchMock, "PUT")).toEqual({ tracing: { tracing: "langsmith" } });
  });

  it("sends a changed project with the overrides already stored", async () => {
    const fetchMock = settingsBackend(
      settingsView({
        tracing: { ...settingsView().tracing, tracing: "langsmith" },
        overrides: { tracing: { tracing: "langsmith" } },
      }),
      [json(settingsView())],
    );
    render(<SettingsPage />);
    const tracing = await panel();

    const project = within(tracing).getByLabelText("Project");
    await userEvent.clear(project);
    await userEvent.type(project, "bleep-prod");
    await userEvent.click(within(tracing).getByRole("button", SAVE));

    expect(sentBody(fetchMock, "PUT")).toEqual({
      tracing: { tracing: "langsmith", langsmith_project: "bleep-prod" },
    });
  });

  it("shows the API's refusals under the form, as it words them", async () => {
    const refusals = [
      "TRACING=langsmith needs LANGSMITH_API_KEY",
      "clear LANGSMITH_API_KEY first: the endpoint changes only while it is unset",
    ];
    settingsBackend(
      settingsView(),
      refusals.map((detail) => json({ detail }, 422)),
    );
    render(<SettingsPage />);
    const tracing = await panel();

    await userEvent.selectOptions(
      within(tracing).getByLabelText("Trace incident runs with"),
      "langsmith",
    );
    await userEvent.click(within(tracing).getByRole("button", SAVE));
    expect(await within(tracing).findByRole("alert")).toHaveTextContent(refusals[0]);

    await userEvent.type(within(tracing).getByLabelText("Endpoint"), "https://eu.example.com");
    await userEvent.click(within(tracing).getByRole("button", SAVE));
    expect(await within(tracing).findByText(refusals[1])).toBeInTheDocument();
    expect(within(tracing).getByLabelText("Endpoint")).toHaveValue("https://eu.example.com");
  });

  it("says a restart is needed while the API lists tracing, in the tracing panel only", async () => {
    settingsBackend(settingsView({ restart_needed: ["tracing"] }));
    render(<SettingsPage />);
    const tracing = await panel();

    expect(within(tracing).getByRole("status")).toHaveTextContent(
      "a change to a running Langfuse tracer applies at the next start",
    );
    expect(
      within(screen.getByRole("region", { name: "Embeddings" })).queryByRole("status"),
    ).toBeNull();
  });

  it("shows what the env sets as set there, with the address as its origin and no input", async () => {
    settingsBackend(
      settingsView({
        tracing: {
          ...settingsView().tracing,
          tracing: "langfuse",
          langfuse_base_url: "http://langfuse-web:3000",
        },
        from_env: ["tracing.tracing", "tracing.langfuse_base_url", "tracing.langsmith_endpoint"],
      }),
    );
    render(<SettingsPage />);
    const tracing = await panel();

    const base = within(tracing).getByRole("group", { name: "Base URL" });
    expect(base).toHaveTextContent("Set in the environment (.env or Compose)");
    expect(base).toHaveTextContent("http://langfuse-web:3000");
    expect(base).toHaveTextContent("LANGFUSE_BASE_URL");
    expect(
      within(tracing).getByRole("group", { name: "Trace incident runs with" }),
    ).toHaveTextContent("langfuse");
    expect(within(tracing).getByRole("group", { name: "Endpoint" })).toBeInTheDocument();
    expect(within(tracing).getByLabelText("UI URL")).toBeInTheDocument();
  });

  it("reads the settings again after a key is saved, so a restart that key needs is said", async () => {
    stubBackend({
      "GET /api/settings": [
        json(settingsView()),
        json(settingsView({ restart_needed: ["tracing"] })),
      ],
      "GET /api/plugins": json([plugin()]),
      "GET /api/telegram/status": json(telegram()),
      "GET /api/secrets": json(secretViews()),
      "PUT /api/secrets/LANGFUSE_PUBLIC_KEY": json(secretViews()),
    });
    render(<SettingsPage />);
    const tracing = await panel();
    expect(within(tracing).queryByRole("status")).toBeNull();

    const key = within(tracing).getByRole("group", { name: "LANGFUSE_PUBLIC_KEY" });
    await userEvent.type(within(key).getByLabelText("Value for LANGFUSE_PUBLIC_KEY"), "pk-lf-1");
    await userEvent.click(within(key).getByRole("button", { name: "Save" }));

    expect(await within(tracing).findByRole("status")).toHaveTextContent("A restart is needed");
  });

  it("links each hint to its input, so a screen reader reads the rule with the field", async () => {
    settingsBackend();
    render(<SettingsPage />);
    const tracing = await panel();
    expect(within(tracing).getByLabelText("Base URL")).toHaveAccessibleDescription(
      /must be https and reach public addresses only/,
    );
    expect(within(tracing).getByLabelText("Trace incident runs with")).toHaveAccessibleDescription(
      "Its keys must be set first.",
    );
  });
});
