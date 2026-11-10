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
    expect(within(coolify).getByRole("textbox", { name: "Deploy Timeout S" })).toHaveAttribute(
      "placeholder",
      "900",
    );
    expect(within(coolify).getAllByRole("textbox")).toHaveLength(1); // no input for the token
    expect(within(coolify).getByText("env COOLIFY_TOKEN")).toBeInTheDocument();
    expect(within(coolify).getAllByText("Missing:")).toHaveLength(2);
  });

  it("saves the settings, leaving an empty optional field to its default", async () => {
    const onSave = vi.fn(async () => undefined);
    render(<PluginList plugins={[plugin()]} onSave={onSave} />);
    await userEvent.type(screen.getByRole("textbox", { name: "Deploy Timeout S" }), "1200");
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(onSave).toHaveBeenCalledWith("coolify", { settings: { deploy_timeout_s: 1200 } }, "p1");
  });

  it("shows why it cannot be switched on", async () => {
    const onSave = vi.fn(async () => {
      throw new ApiError(422, "plugin coolify needs COOLIFY_TOKEN set in the environment first");
    });
    render(<PluginList plugins={[plugin()]} onSave={onSave} />);
    await userEvent.click(screen.getByRole("button", { name: "Switch on" }));
    expect(onSave).toHaveBeenCalledWith("coolify", { enabled: true }, "p1");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "plugin coolify needs COOLIFY_TOKEN set in the environment first",
    );
  });

  it("shows a switched-on plugin's health and switches it off", async () => {
    const onSave = vi.fn(async () => undefined);
    const on = plugin({
      enabled: true,
      switched_on: true,
      settings: { deploy_timeout_s: 900 },
      secrets: [{ env: "COOLIFY_TOKEN", set: true, required: true }],
      health: { ok: true, detail: "3 applications" },
    });
    render(<PluginList plugins={[on]} onSave={onSave} />);
    expect(screen.getByText("Healthy")).toBeInTheDocument();
    expect(screen.getByText("3 applications")).toBeInTheDocument();
    expect(screen.getByText("Set:")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save settings" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Switch off" }));
    expect(onSave).toHaveBeenCalledWith("coolify", { enabled: false }, "p1");
  });

  it("shows a plugin switched on but waiting for its secret, and switches it off", async () => {
    const onSave = vi.fn(async () => undefined);
    const waiting = plugin({
      switched_on: true,
      settings: { deploy_timeout_s: 900 },
      secrets: [
        { env: "COOLIFY_TOKEN", set: false, required: true },
        { env: "COOLIFY_CA", set: false, required: false },
      ],
    });
    render(<PluginList plugins={[waiting]} onSave={onSave} />);
    expect(screen.getByText("On, waiting for COOLIFY_TOKEN")).toBeInTheDocument();
    expect(screen.getByText(/does not run until COOLIFY_TOKEN is set/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Switch on" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Switch off" }));
    expect(onSave).toHaveBeenCalledWith("coolify", { enabled: false }, "p1");
  });

  it("shows an unset optional secret neutrally and a required one as missing", () => {
    const p = plugin({
      secrets: [
        { env: "MCP_URL", set: false, required: true },
        { env: "MCP_TOKEN", set: false, required: false },
      ],
    });
    render(<PluginList plugins={[p]} onSave={vi.fn()} />);
    expect(screen.getAllByText("Missing:")).toHaveLength(1);
    expect(screen.getByText("Not set, optional:")).toBeInTheDocument();
  });

  it("says a plugin switched on with every secret set is not running", () => {
    const broken = plugin({
      switched_on: true,
      secrets: [{ env: "COOLIFY_TOKEN", set: true, required: true }],
    });
    render(<PluginList plugins={[broken]} onSave={vi.fn()} />);
    expect(screen.getByText("On, not running")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Switch off" })).toBeInTheDocument();
  });

  it("keeps a boolean's default of true when the person leaves it untouched", async () => {
    const onSave = vi.fn(async () => undefined);
    const p = plugin({
      settings_schema: {
        properties: {
          url: { type: "string", title: "Url" },
          verify_tls: { type: "boolean", title: "Verify TLS", default: true },
          optional_flag: { anyOf: [{ type: "boolean" }, { type: "null" }], title: "Flag" },
        },
        required: ["url"],
      },
    });
    render(<PluginList plugins={[p]} onSave={onSave} />);
    expect(screen.getByRole("checkbox", { name: "Verify TLS" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Flag" })).not.toBeChecked();

    await userEvent.type(screen.getByRole("textbox", { name: "Url" }), "https://c.lan");
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(onSave).toHaveBeenCalledWith(
      "coolify",
      {
        settings: { url: "https://c.lan", verify_tls: true },
      },
      "p1",
    );
  });

  it("sends a boolean the person switched off as false", async () => {
    const onSave = vi.fn(async () => undefined);
    const p = plugin({
      settings: { url: "https://c.lan", verify_tls: true },
      settings_schema: {
        properties: {
          url: { type: "string", title: "Url" },
          verify_tls: { type: "boolean", title: "Verify TLS", default: true },
        },
      },
    });
    render(<PluginList plugins={[p]} onSave={onSave} />);
    await userEvent.click(screen.getByRole("checkbox", { name: "Verify TLS" }));
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(onSave).toHaveBeenCalledWith(
      "coolify",
      {
        settings: { url: "https://c.lan", verify_tls: false },
      },
      "p1",
    );
  });

  it("never renders a SecretStr field, whatever its name", () => {
    const p = plugin({
      settings_schema: {
        properties: {
          url: { type: "string", title: "Url" },
          auth: { type: "string", format: "password", writeOnly: true, title: "Auth" },
          other: {
            anyOf: [{ type: "string", format: "password", writeOnly: true }, { type: "null" }],
            title: "Other",
          },
        },
      },
    });
    render(<PluginList plugins={[p]} onSave={vi.fn()} />);
    expect(screen.getAllByRole("textbox")).toHaveLength(1);
    expect(screen.queryByText("Auth")).toBeNull();
    expect(screen.queryByText("Other")).toBeNull();
  });

  it("edits the MCP server's purpose as one capped line with an example, and clears it", async () => {
    const onSave = vi.fn(async () => undefined);
    const example = "Manages the Coolify server: apps, deployments, env settings";
    const mcp = plugin({
      name: "mcp",
      title: "MCP server by URL",
      settings: { purpose: "Manages Caddy", read_tools: ["caddy_config"], act_tools: [] },
      settings_schema: {
        properties: {
          purpose: {
            type: "string",
            title: "Purpose",
            default: "",
            maxLength: 200,
            examples: [example],
            description: "What the server is for, in a line.",
          },
          read_tools: { type: "array", title: "Read Tools", items: { type: "string" } },
          act_tools: { type: "array", title: "Act Tools", items: { type: "string" } },
        },
      },
    });
    render(<PluginList plugins={[mcp]} onSave={onSave} />);

    const purpose = screen.getByRole("textbox", { name: "Purpose" });
    expect(purpose).toHaveValue("Manages Caddy");
    expect(purpose).toHaveAttribute("maxLength", "200");
    expect(screen.getByText("What the server is for, in a line.")).toBeInTheDocument();

    await userEvent.clear(purpose);
    expect(purpose).toHaveAttribute("placeholder", example);
    await userEvent.type(purpose, "  Manages Coolify ");
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(onSave).toHaveBeenLastCalledWith(
      "mcp",
      { settings: { purpose: "Manages Coolify", read_tools: ["caddy_config"], act_tools: [] } },
      "p1",
    );

    await userEvent.clear(screen.getByRole("textbox", { name: "Purpose" }));
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(onSave).toHaveBeenLastCalledWith(
      "mcp",
      { settings: { read_tools: ["caddy_config"], act_tools: [] } },
      "p1",
    );
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
