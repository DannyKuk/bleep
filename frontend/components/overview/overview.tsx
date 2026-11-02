"use client";

import { Loader2, ServerOff, TriangleAlert } from "lucide-react";

import { HostCard } from "@/components/overview/host-card";
import { usePoll } from "@/hooks/use-poll";
import type { HostView, ServiceView } from "@/lib/api-types";

const SETUP_DOCS = "https://github.com/DannyKuk/bleep/blob/main/docs/setup.md";

export function OverviewView({ hosts, services }: { hosts: HostView[]; services: ServiceView[] }) {
  if (hosts.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border px-6 py-12 text-center">
        <ServerOff aria-hidden className="size-6 text-muted-foreground" />
        <p className="text-sm font-medium">
          No hosts configured. Add one in <code className="font-mono">config/hosts.yaml</code>
        </p>
        <a
          href={SETUP_DOCS}
          className="text-sm text-muted-foreground underline underline-offset-2 hover:text-foreground"
        >
          Setup guide
        </a>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-4">
      {hosts.map((host) => (
        <HostCard
          key={host.id}
          host={host}
          services={services.filter((service) => service.host_id === host.id)}
        />
      ))}
    </div>
  );
}

const POLL_MS = 10_000;

export function Overview() {
  const hosts = usePoll<HostView[]>("/api/hosts", POLL_MS);
  const services = usePoll<ServiceView[]>("/api/services", POLL_MS);

  const error = hosts.error ?? services.error;
  if (!hosts.data || !services.data) {
    if (error) {
      return (
        <p role="alert" className="flex items-center gap-2 text-sm text-health-down">
          <TriangleAlert aria-hidden className="size-4" />
          Could not load hosts: {error.message}
        </p>
      );
    }
    return (
      <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 aria-hidden className="size-4 animate-spin" />
        Loading hosts
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-4">
      {error && (
        <p role="alert" className="flex items-center gap-2 text-xs text-health-degraded">
          <TriangleAlert aria-hidden className="size-3.5" />
          Refresh failed, showing the last data: {error.message}
        </p>
      )}
      <OverviewView hosts={hosts.data} services={services.data} />
    </div>
  );
}
