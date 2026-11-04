import Link from "next/link";

import { Fact, SecretBadge } from "@/components/settings/secret";
import type { ModelChoice, SecretStatus } from "@/lib/settings-types";

const PROVIDERS: Record<ModelChoice["provider"], string> = {
  openrouter: "OpenRouter",
  local: "Local (OpenAI-compatible server)",
};

/** The models in force, read-only: the model choice and the budgets are changed in the dev panel. */
export function ModelSettings({ models, apiKey }: { models: ModelChoice; apiKey: SecretStatus }) {
  const local = models.provider === "local";
  return (
    <div className="flex flex-col gap-3">
      <dl className="flex flex-col gap-1.5">
        <Fact label="Provider">{PROVIDERS[models.provider]}</Fact>
        {local ? (
          <Fact label="Model">
            <code className="font-mono">{models.local_model}</code>
          </Fact>
        ) : (
          <>
            <Fact label="Model">
              <code className="font-mono">{models.model}</code>
            </Fact>
            <Fact label="Cheap model">
              <code className="font-mono">{models.cheap_model}</code>
            </Fact>
          </>
        )}
        <Fact label="API key">
          <SecretBadge secret={apiKey} />
          {local && (
            <span className="ml-2 text-xs text-muted-foreground">(not needed locally)</span>
          )}
        </Fact>
      </dl>
      <p className="text-xs text-muted-foreground">
        Change the models and the budgets in the{" "}
        <Link href="/dev" className="underline underline-offset-2 hover:text-foreground">
          dev panel
        </Link>
        .
      </p>
    </div>
  );
}
