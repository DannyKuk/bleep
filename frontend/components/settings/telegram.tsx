import { Fact, SecretBadge } from "@/components/settings/secret";
import { Badge } from "@/components/ui/badge";
import { formatDateTime } from "@/lib/format";
import type { TelegramStatus } from "@/lib/settings-types";

/** Whether Telegram is set up and polling. Its token and chat id are set under Secrets, or in `.env`. */
export function TelegramSettings({
  status,
  tokenSource,
}: {
  status: TelegramStatus;
  /** Where the token is kept, from `GET /api/secrets`. */
  tokenSource?: "env" | "dashboard" | null;
}) {
  return (
    <div className="flex flex-col gap-3">
      <dl className="flex flex-col gap-1.5">
        <Fact label="Status">
          <Badge>{status.configured ? "Configured" : "Not configured"}</Badge>
        </Fact>
        <Fact label="Bot token">
          <SecretBadge secret={status.token} source={tokenSource} />
        </Fact>
        <Fact label="Chat">
          {status.chat_id ? (
            <code className="font-mono">{status.chat_id}</code>
          ) : (
            <span className="text-muted-foreground">
              Missing: <code className="font-mono">env TELEGRAM_CHAT_ID</code>
            </span>
          )}
        </Fact>
        {status.configured && (
          <Fact label="Last poll">
            {status.last_poll ? (
              <time dateTime={status.last_poll}>{formatDateTime(status.last_poll)}</time>
            ) : (
              <span className="text-muted-foreground">Not yet</span>
            )}
          </Fact>
        )}
      </dl>
      {!status.configured && (
        <p className="text-xs text-muted-foreground">
          Without Telegram every approval happens here. Set{" "}
          <code className="font-mono">TELEGRAM_BOT_TOKEN</code> and{" "}
          <code className="font-mono">TELEGRAM_CHAT_ID</code> under Secrets below, or in{" "}
          <code className="font-mono">.env</code> and restart Bleep, to get messages and approve
          from the chat.
        </p>
      )}
    </div>
  );
}
