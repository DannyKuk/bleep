"use client";

import { CheckCircle2, XCircle } from "lucide-react";
import { useState } from "react";

import { Panel } from "@/components/panel";
import { HostForm, RemoveHost } from "@/components/settings/host-form";
import { ListEditor, SourceNote } from "@/components/settings/rules";
import { SaveError, useSaving } from "@/components/settings/save-error";
import { Fact, SecretBadge } from "@/components/settings/secret";
import { Button } from "@/components/ui/button";
import type { HostConnection, HostOverrides, HostSettingsView } from "@/lib/settings-types";
import { cn } from "@/lib/utils";

const RULE_LABELS = { rules: "Rule" };
/** The host's overrides with its own rules replaced, or back to the hosts file's (undefined). */
export function withHostRules(host: HostOverrides | undefined, rules?: string[]): HostOverrides {
  const next = { ...host, rules };
  if (rules === undefined) delete next.rules;
  return next;
}

/** The host's overrides with `may_sleep` set, or back to the hosts file's (undefined). */
export function withMaySleep(host: HostOverrides | undefined, maySleep?: boolean): HostOverrides {
  const next = { ...host, may_sleep: maySleep };
  if (maySleep === undefined) delete next.may_sleep;
  return next;
}

function MaySleep({
  host,
  overrides,
  save,
}: {
  host: HostSettingsView;
  overrides: HostOverrides | undefined;
  save: (host: HostOverrides) => Promise<unknown>;
}) {
  const { busy, problems, run } = useSaving();
  return (
    <div className="flex flex-col gap-2">
      {host.connection.source === "file" && (
        <SourceNote
          overridden={overrides?.may_sleep !== undefined}
          disabled={busy}
          onReset={() => void run(() => save(withMaySleep(overrides)))}
        />
      )}
      <label className="flex min-h-11 items-center gap-2 text-sm font-bold">
        <input
          type="checkbox"
          checked={host.may_sleep}
          disabled={busy}
          onChange={(changed) =>
            void run(() => save(withMaySleep(overrides, changed.target.checked)))
          }
          className="size-4"
        />
        This host may sleep
      </label>
      <p className="text-xs text-muted-foreground">
        For a laptop: while it does not answer, it shows as asleep and Bleep opens no incident for
        it.
      </p>
      <SaveError problems={problems} />
    </div>
  );
}

function KeyState({ what, set }: { what: string; set: boolean }) {
  return (
    <span
      data-set={set}
      className={cn(
        "inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium",
        set ? "bg-health-ok/12 text-health-ok" : "bg-health-down/12 text-health-down",
      )}
    >
      {set ? (
        <CheckCircle2 aria-hidden className="size-3.5" />
      ) : (
        <XCircle aria-hidden className="size-3.5" />
      )}
      {what}: {set ? "set" : "missing"}
    </span>
  );
}

function Connection({ connection }: { connection: HostConnection }) {
  if (connection.kind === "socket") {
    return (
      <dl className="flex flex-col gap-1.5">
        <Fact label="Kind">Docker socket</Fact>
        <Fact label="Endpoint">
          <code className="font-mono">{connection.endpoint}</code>
        </Fact>
      </dl>
    );
  }
  return (
    <dl className="flex flex-col gap-1.5">
      <Fact label="Kind">SSH</Fact>
      <Fact label="Address">
        <code className="font-mono">
          {connection.user}@{connection.address}:{connection.port}
        </code>
      </Fact>
      <Fact label="Docker socket">
        <code className="font-mono">{connection.docker_socket}</code>
      </Fact>
      {connection.key_file && (
        <Fact label="SSH key">
          <SecretBadge secret={connection.key_file} />
        </Fact>
      )}
      {connection.ssh_key && (
        <Fact label="SSH key">
          <KeyState what="Key" set={connection.ssh_key.set} />
        </Fact>
      )}
      {connection.host_key && (
        <Fact label="Host key">
          <KeyState what="Host key" set={connection.host_key.set} />
        </Fact>
      )}
      {connection.known_hosts_file && (
        <Fact label="Known hosts">
          <code className="font-mono">{connection.known_hosts_file}</code>
        </Fact>
      )}
    </dl>
  );
}

/**
 * One host's settings as three panels: how Bleep reaches it (edit and remove for a dashboard
 * host), whether it may sleep, and its rules. Each service's settings are edited on the service's
 * page.
 */
export function HostSettings({
  host,
  overrides,
  save,
  onHostChanged,
  onRemoved,
}: {
  host: HostSettingsView;
  overrides: HostOverrides | undefined;
  save: (host: HostOverrides) => Promise<unknown>;
  /** Reads the settings again after the host was edited, or after a stale edit or removal. */
  onHostChanged?: () => Promise<void>;
  /** Called once the host is removed; without it the settings are read again. */
  onRemoved?: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const fromDashboard = host.connection.source === "dashboard" && onHostChanged !== undefined;
  return (
    <>
      <Panel title="Connection">
        <div className="flex flex-col gap-1.5">
          <p className="text-xs text-muted-foreground">
            {fromDashboard ? "Added on the dashboard" : "From config/hosts.yaml; change it there"}
          </p>
          <Connection connection={host.connection} />
          {fromDashboard &&
            (editing ? (
              <HostForm
                key={host.connection.version}
                host={host}
                onChanged={onHostChanged}
                onClose={() => setEditing(false)}
              />
            ) : (
              <div className="flex flex-col gap-2 pt-1">
                <div>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => setEditing(true)}
                  >
                    Edit connection
                  </Button>
                </div>
                <RemoveHost host={host} onChanged={onHostChanged} onRemoved={onRemoved} />
              </div>
            ))}
        </div>
      </Panel>
      <Panel title="Sleep">
        <MaySleep host={host} overrides={overrides} save={save} />
      </Panel>
      <Panel title="Rules for every service">
        <ListEditor
          key={JSON.stringify(host.rules)}
          label={`Rules for ${host.name}`}
          items={host.rules}
          addLabel="Add a rule for this host"
          emptyText="No host rules."
          labels={RULE_LABELS}
          overridden={overrides?.rules !== undefined}
          onSave={(rules) => save(withHostRules(overrides, rules))}
          onReset={() => save(withHostRules(overrides))}
        />
      </Panel>
    </>
  );
}
