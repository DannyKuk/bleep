import {
  AlertTriangle,
  CheckCheck,
  CheckCircle2,
  HelpCircle,
  Moon,
  PauseCircle,
  XCircle,
  type LucideIcon,
} from "lucide-react";

import type { HostHealth } from "@/lib/api-types";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

// Colour is never the only signal: every health has its own icon and word too. The full class
// names are written out so Tailwind sees them.
const HEALTH: Record<HostHealth, { icon: LucideIcon; className: string }> = {
  ok: { icon: CheckCircle2, className: "bg-health-ok-bg text-health-ok" },
  degraded: {
    icon: AlertTriangle,
    className: "bg-health-degraded-bg text-health-degraded",
  },
  down: { icon: XCircle, className: "bg-health-down-bg text-health-down" },
  stopped: {
    icon: PauseCircle,
    className: "bg-health-stopped-bg text-health-stopped",
  },
  unknown: {
    icon: HelpCircle,
    className: "bg-health-unknown-bg text-health-unknown",
  },
  done: {
    icon: CheckCheck,
    className: "bg-health-stopped-bg text-health-stopped",
  },
  asleep: {
    icon: Moon,
    className: "bg-health-stopped-bg text-health-stopped",
  },
};

export function HealthBadge({
  health,
  label,
  className,
}: {
  health: HostHealth;
  /** Overrides the default word (URL checks say "Failing", not "Down"). */
  label?: string;
  className?: string;
}) {
  const { icon: Icon, className: tone } = HEALTH[health];
  const defaultLabel = useT().health.states[health];
  return (
    <span
      data-health={health}
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-3 py-1 text-[13px] font-bold",
        tone,
        className,
      )}
    >
      <Icon aria-hidden className="size-3.5" />
      {label ?? defaultLabel}
    </span>
  );
}
