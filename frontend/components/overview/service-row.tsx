import { HealthBadge } from "@/components/overview/health-badge";
import type { ContainerView, Health, UrlStatus } from "@/lib/api-types";
import { formatTime } from "@/lib/format";
import { cn } from "@/lib/utils";

const CONTAINER_TONE: Record<ContainerView["health"], string> = {
  healthy: "text-health-ok",
  starting: "text-health-degraded",
  unhealthy: "text-health-down",
  none: "text-muted-foreground",
};

function stateTone(container: ContainerView): string {
  if (container.health === "unhealthy") return "text-health-down";
  if (container.state === "running") return CONTAINER_TONE[container.health];
  if (container.state === "restarting") return "text-health-degraded";
  // Exited 0 or by SIGTERM: stopped on purpose or finished, not a crash.
  if (container.state === "exited" && (container.exit_code === 0 || container.exit_code === 143)) {
    return "text-health-stopped";
  }
  return "text-health-down";
}

export function ContainerChip({ container }: { container: ContainerView }) {
  const detail =
    container.health === "none" ? container.state : `${container.state}, ${container.health}`;
  return (
    <li
      title={`${container.image} (${container.id.slice(0, 12)})`}
      className="inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-0.5 text-xs"
    >
      <span className="font-mono">{container.name}</span>
      <span className={cn("font-medium", stateTone(container))}>{detail}</span>
    </li>
  );
}

const URL_HEALTH: Record<UrlStatus["status"], { label: string; health: Health }> = {
  ok: { label: "OK", health: "ok" },
  failing: { label: "Failing", health: "down" },
  unknown: { label: "Not checked yet", health: "unknown" },
};

export function UrlItem({ url }: { url: UrlStatus }) {
  const { label: statusLabel, health } = URL_HEALTH[url.status];
  const label =
    url.status === "unknown" && url.last_checked != null ? "No recent check" : statusLabel;
  return (
    <li className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
      <span className="font-mono break-all">{url.url}</span>
      <HealthBadge health={health} label={label} />
      {(url.status_code != null || url.latency_ms != null || url.last_checked != null) && (
        <span className="text-muted-foreground">
          {[
            url.status_code,
            url.latency_ms != null ? `${url.latency_ms} ms` : null,
            url.last_checked != null ? `checked ${formatTime(url.last_checked)}` : null,
          ]
            .filter((part) => part != null)
            .join(" · ")}
        </span>
      )}
    </li>
  );
}
