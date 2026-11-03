import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { plugin } from "@/components/settings/fixtures";
import { kindOf, PluginList } from "@/components/settings/plugins";
import { ApiError } from "@/lib/api";

describe("PluginList", () => {
  it("builds the settings form from the schema and shows secrets only as set or missing", () => {
    render(<PluginList plugins={[plugin()]} onSave={vi.fn()} />);
    const coolify = screen.getByRole("region", { name: "Plugin Coolify" });
    expect(within(coolify).getByRole("textbox", { name: "Url" })).toHaveValue("");
    expect(within(coolify).getByRole("textbox", { name: "Team" })).toHaveAttribute(
      "placeholder",
      "main",
    );
    expect(within(coolify).getAllByRole("textbox")).toHaveLength(2); // no input for the token
    expect(within(coolify).getByText("env COOLIFY_TOKEN")).toBeInTheDocument();
    expect(within(coolify).getByText("Missing:")).toBeInTheDocument();
  });

  it("saves the settings, leaving an empty optional field to its default", async () => {
    const onSave = vi.fn(async () => undefined);
    render(<PluginList plugins={[plugin()]} onSave={onSave} />);
    await userEvent.type(screen.getByRole("textbox", { name: "Url" }), "https://coolify.lan");
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(onSave).toHaveBeenCalledWith("coolify", {
      settings: { url: "https://coolify.lan" },
    });
  });

  it("shows why it cannot be switched on", async () => {
    const onSave = vi.fn(async () => {
      throw new ApiError(422, "plugin coolify needs COOLIFY_TOKEN set in the environment first");
    });
    render(<PluginList plugins={[plugin()]} onSave={onSave} />);
    await userEvent.click(screen.getByRole("button", { name: "Switch on" }));
    expect(onSave).toHaveBeenCalledWith("coolify", { enabled: true });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "plugin coolify needs COOLIFY_TOKEN set in the environment first",
    );
  });

  it("shows a switched-on plugin's health and switches it off", async () => {
    const onSave = vi.fn(async () => undefined);
    const on = plugin({
      enabled: true,
      settings: { url: "https://coolify.lan", team: "main" },
      secrets: [{ env: "COOLIFY_TOKEN", set: true }],
      health: { ok: true, detail: "3 applications" },
    });
    render(<PluginList plugins={[on]} onSave={onSave} />);
    expect(screen.getByText("Healthy")).toBeInTheDocument();
    expect(screen.getByText("3 applications")).toBeInTheDocument();
    expect(screen.getByText("Set via")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save settings" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Switch off" }));
    expect(onSave).toHaveBeenCalledWith("coolify", { enabled: false });
  });

  it("says when Bleep has no plugins", () => {
    render(<PluginList plugins={[]} onSave={vi.fn()} />);
    expect(screen.getByText("This Bleep has no plugins.")).toBeInTheDocument();
  });
});

describe("kindOf", () => {
  it("reads an optional field's type from anyOf, and a list of strings", () => {
    expect(kindOf({ anyOf: [{ type: "integer" }, { type: "null" }] })).toBe("integer");
    expect(kindOf({ type: "array", items: { type: "string" } })).toBe("list");
    expect(kindOf({ type: "object" })).toBe("other");
  });
});
