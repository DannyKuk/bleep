import { CheckCircle2, MinusCircle, XCircle } from "lucide-react";

import type { SecretStatus } from "@/lib/settings-types";
import { cn } from "@/lib/utils";

/**
 * Where a secret comes from and whether it is there; never its value, which no API returns.
 * `source` is where `GET /api/secrets` says a variable's value is kept; without it only the
 * variable is named, so the badge does not claim a place.
 */
export function SecretBadge({
  secret,
  source,
  optional = false,
}: {
  secret: SecretStatus;
  source?: "env" | "dashboard" | null;
  /** Unset is then fine, not broken: shown neutrally. */
  optional?: boolean;
}) {
  const named = secret.env ? `env ${secret.env}` : `file ${secret.file ?? ""}`;
  const where =
    source === "env"
      ? "Set in .env: "
      : source === "dashboard"
        ? "Set on the dashboard: "
        : "Set: ";
  const neutral = optional && !secret.set;
  const Icon = secret.set ? CheckCircle2 : neutral ? MinusCircle : XCircle;
  return (
    <span
      data-set={secret.set}
      className={cn(
        "inline-flex max-w-full items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium",
        secret.set && "bg-health-ok/12 text-health-ok",
        neutral && "bg-muted text-muted-foreground",
        !secret.set && !neutral && "bg-health-down/12 text-health-down",
      )}
    >
      <Icon aria-hidden className="size-3.5 shrink-0" />
      <span className="min-w-0 break-all">
        {secret.set ? where : neutral ? "Not set, optional: " : "Missing: "}
        <code className="font-mono">{named}</code>
      </span>
    </span>
  );
}

/** One read-only line of a definition list. */
export function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
      <dt className="w-32 shrink-0 text-xs text-muted-foreground">{label}</dt>
      <dd className="min-w-0 text-sm break-words">{children}</dd>
    </div>
  );
}
