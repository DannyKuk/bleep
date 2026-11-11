import { CodeText } from "@/components/code-text";
import { Fact, SecretBadge } from "@/components/settings/secret";
import { useTimeZone } from "@/components/preferences";
import { Badge } from "@/components/ui/badge";
import { formatDateTime } from "@/lib/format";
import { useLanguage, useT } from "@/lib/i18n";
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
  const zone = useTimeZone();
  const language = useLanguage();
  const t = useT().settings.telegram;
  return (
    <div className="flex flex-col gap-3">
      <dl className="flex flex-col gap-1.5">
        <Fact label={t.status}>
          <Badge>{status.configured ? t.configured : t.notConfigured}</Badge>
        </Fact>
        <Fact label={t.token}>
          <SecretBadge secret={status.token} source={tokenSource} />
        </Fact>
        <Fact label={t.chat}>
          {status.chat_id ? (
            <code className="font-mono">{status.chat_id}</code>
          ) : (
            <span className="text-muted-foreground">
              <CodeText text={t.chatMissing} />
            </span>
          )}
        </Fact>
        {status.configured && (
          <Fact label={t.lastPoll}>
            {status.last_poll ? (
              <time dateTime={status.last_poll}>
                {formatDateTime(status.last_poll, zone, language)}
              </time>
            ) : (
              <span className="text-muted-foreground">{t.notYet}</span>
            )}
          </Fact>
        )}
      </dl>
      {!status.configured && (
        <p className="text-xs text-muted-foreground">
          <CodeText text={t.without} />
        </p>
      )}
    </div>
  );
}
