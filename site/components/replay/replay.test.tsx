import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Replay } from "./replay";

/** Moves time on in small steps, each in its own act, so React renders between the replay's
 * timers and each new state can schedule the next one. */
async function advance(ms: number) {
  for (let passed = 0; passed < ms; passed += 250) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
  }
}

describe("Replay", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  it("waits for the visitor's yes however long they take", async () => {
    render(<Replay />);
    await advance(60_000);
    expect(screen.getByText("Needs your yes")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Yes, start it" })).toBeInTheDocument();
    expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toContain(
      'inspect_container("shop-web")health check failing since shop-db stopped',
    );
  });

  it("fixes and verifies after a yes, and can play again", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Replay />);
    await advance(60_000);
    await user.click(screen.getByRole("button", { name: "Yes, start it" }));
    expect(screen.getByText("Running the fix")).toBeInTheDocument();
    await advance(10_000);
    expect(screen.getByText("Fixed and checked")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Play it again" }));
    expect(screen.getByText("Watching")).toBeInTheDocument();
  });

  it("leaves everything as it is after a no", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Replay />);
    await advance(60_000);
    await user.click(screen.getByRole("button", { name: "Not now" }));
    await advance(60_000);
    expect(screen.getByText("Nothing ran.")).toBeInTheDocument();
    expect(screen.getByText("stopped")).toBeInTheDocument();
  });
});
