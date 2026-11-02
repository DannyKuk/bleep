import { AlertTriangle, CheckCircle2, HelpCircle, XCircle, type LucideIcon } from "lucide-react";

import type { Health } from "@/lib/api-types";
import { cn } from "@/lib/utils";

// Colour is never the only signal: every health has its own icon and word too. The full class
// names are written out so Tailwind sees them.
const HEALTH: Record<Health, { label: string; icon: LucideIcon; className: string }> = {
  ok: { label: "OK", icon: CheckCircle2, className: "bg-health-ok/12 text-health-ok" },
  degraded: {
    label: "Degraded",
    icon: AlertTriangle,
    className: "bg-health-degraded/12 text-health-degraded",
  },
  down: { label: "Down", icon: XCircle, className: "bg-health-down/12 text-health-down" },
  unknown: {
    label: "Unknown",
    icon: HelpCircle,
    className: "bg-health-unknown/12 text-health-unknown",
  },
};

export function HealthBadge({
  health,
  label,
  className,
}: {
  health: Health;
  /** Overrides the default word (URL checks say "Failing", not "Down"). */
  label?: string;
  className?: string;
}) {
  const { label: defaultLabel, icon: Icon, className: tone } = HEALTH[health];
  return (
    <span
      data-health={health}
      className={cn(
        "inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium",
        tone,
        className,
      )}
    >
      <Icon aria-hidden className="size-3.5" />
      {label ?? defaultLabel}
    </span>
  );
}
