import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { invalid, THRESHOLD_VALUES } from "@/components/settings/fixtures";
import { ThresholdsForm } from "@/components/settings/thresholds";
import { ApiError } from "@/lib/api";

const field = (name: RegExp) => screen.getByRole("textbox", { name });

describe("ThresholdsForm", () => {
  it("saves the changed value with the ones already changed here", async () => {
    const onSave = vi.fn(async () => undefined);
    render(
      <ThresholdsForm
        values={{ ...THRESHOLD_VALUES, debounce_s: 45 }}
        overrides={{ debounce_s: 45 }}
        onSave={onSave}
      />,
    );
    expect(screen.getByText("(changed here)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save thresholds" })).toBeDisabled();

    await userEvent.clear(field(/^Disk used at or above/));
    await userEvent.type(field(/^Disk used at or above/), "85");
    await userEvent.click(screen.getByRole("button", { name: "Save thresholds" }));

    expect(onSave).toHaveBeenCalledWith({ debounce_s: 45, disk_percent: 85 });
  });

  it("shows the API's bound under the form, named as the form names the field", async () => {
    const reply = invalid(
      ["thresholds", "disk_percent"],
      "Input should be less than or equal to 99",
    );
    const body = await reply.json();
    const onSave = vi.fn(async () => {
      throw new ApiError(422, JSON.stringify(body.detail), undefined, body);
    });
    render(<ThresholdsForm values={THRESHOLD_VALUES} overrides={{}} onSave={onSave} />);

    await userEvent.clear(field(/^Disk used at or above/));
    await userEvent.type(field(/^Disk used at or above/), "100");
    await userEvent.click(screen.getByRole("button", { name: "Save thresholds" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Disk used at or above: Input should be less than or equal to 99",
    );
    expect(field(/^Disk used at or above/)).toHaveValue("100");
  });

  it("sends a value that is not a number as typed, for the API to refuse", async () => {
    const onSave = vi.fn(async () => undefined);
    render(<ThresholdsForm values={THRESHOLD_VALUES} overrides={{}} onSave={onSave} />);
    await userEvent.clear(field(/^Load per CPU above/));
    await userEvent.type(field(/^Load per CPU above/), "lots");
    await userEvent.click(screen.getByRole("button", { name: "Save thresholds" }));
    expect(onSave).toHaveBeenCalledWith({ load_per_cpu: "lots" });
  });

  it("goes back to the defaults by clearing the section", async () => {
    const onSave = vi.fn(async () => undefined);
    render(
      <ThresholdsForm
        values={{ ...THRESHOLD_VALUES, disk_percent: 80 }}
        overrides={{ disk_percent: 80 }}
        onSave={onSave}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Back to the defaults" }));
    expect(onSave).toHaveBeenCalledWith({});
  });
});
