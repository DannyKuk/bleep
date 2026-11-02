import { HealthBadge } from "@/components/overview/health-badge";
import type { Health, IncidentStatus } from "@/lib/api-types";

// An incident's status in the dashboard's health colours: red when it needs a human or failed,
// amber while Bleep works on it, green when fixed, grey when closed.
const STATUS: Record<IncidentStatus, { health: Health; label: string }> = {
  open: { health: "degraded", label: "Open" },
  investigating: { health: "degraded", label: "Investigating" },
  awaiting_approval: { health: "down", label: "Awaiting approval" },
  executing: { health: "degraded", label: "Executing" },
  verifying: { health: "degraded", label: "Verifying" },
  resolved: { health: "ok", label: "Resolved" },
  closed: { health: "stopped", label: "Closed" },
  failed: { health: "down", label: "Failed" },
};

export function IncidentStatusBadge({ status }: { status: IncidentStatus }) {
  const { health, label } = STATUS[status];
  return <HealthBadge health={health} label={label} />;
}
