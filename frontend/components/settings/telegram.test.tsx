import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { telegram } from "@/components/settings/fixtures";
import { TelegramSettings } from "@/components/settings/telegram";

describe("TelegramSettings", () => {
  it("shows a configured bot with the chat masked and the last poll", () => {
    render(<TelegramSettings status={telegram()} />);
    expect(screen.getByText("Configured")).toBeInTheDocument();
    expect(screen.getByText("••••90")).toBeInTheDocument();
    expect(screen.getByText("env TELEGRAM_BOT_TOKEN")).toBeInTheDocument();
    expect(screen.getByText(/2026/).closest("time")).toHaveAttribute(
      "datetime",
      "2026-11-03T10:00:00Z",
    );
  });

  it("says a bot that has not polled yet has not", () => {
    render(<TelegramSettings status={telegram({ last_poll: null })} />);
    expect(screen.getByText("Not yet")).toBeInTheDocument();
  });

  it("says what is missing without a token", () => {
    render(
      <TelegramSettings
        status={telegram({
          configured: false,
          chat_id: null,
          last_poll: null,
          token: { env: "TELEGRAM_BOT_TOKEN", file: null, set: false },
        })}
      />,
    );
    expect(screen.getByText("Not configured")).toBeInTheDocument();
    expect(screen.getAllByText("Missing:")).toHaveLength(2); // the token and the chat
    expect(screen.getByText("env TELEGRAM_CHAT_ID")).toBeInTheDocument();
    expect(screen.queryByText("Last poll")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
  });
});
