"use client";

import { CheckCircle2, Lock, XCircle } from "lucide-react";
import { useState } from "react";

import { Panel } from "@/components/panel";
import { HostForm, RemoveHost } from "@/components/settings/host-form";
import { ListEditor } from "@/components/settings/rules";
import { Fact, SecretBadge } from "@/components/settings/secret";
import { UrlEditor } from "@/components/settings/urls";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type {
  HostConnection,
  HostOverrides,
  HostSettingsView,
  ServiceOverrides,
  ServiceSettingsView,
} from "@/lib/settings-types";
import { cn } from "@/lib/utils";

const RULE_LABELS = { rules: "Rule" };
const CONTAINER_LABELS = { protected_containers: "Container" };
const NOTHING: ServiceSettingsView = {
  urls: [],
  rules: [],
  protected_containers: [],
  hosts_file_protected_containers: [],
};

/** A service Bleep sees on the host, by its key there (`compose:shop`). */
export interface KnownService {
  key: string;
  name: string;
}

/**
 * The host's overrides with one service's fields changed; a field set to undefined goes back to
 * the hosts file's, and a service left with nothing of its own is dropped.
 */
export function withService(
  host: HostOverrides | undefined,
  key: string,
  change: { [field in keyof ServiceOverrides]?: ServiceOverrides[field] | undefined },
): HostOverrides {
  const service: ServiceOverrides = { ...host?.services?.[key], ...change };
  for (const field of Object.keys(service) as (keyof ServiceOverrides)[]) {
    if (service[field] === undefined) delete service[field];
  }
  const services = { ...host?.services };
  if (Object.keys(service).length > 0) services[key] = service;
  else delete services[key];
  return { ...host, services };
}

/** The host's overrides with its own rules replaced, or back to the hosts file's (undefined). */
export function withHostRules(host: HostOverrides | undefined, rules?: string[]): HostOverrides {
  const next = { ...host, rules };
  if (rules === undefined) delete next.rules;
  return next;
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

function ServiceSettings({
  serviceKey,
  name,
  view,
  overrides,
  save,
}: {
  serviceKey: string;
  name: string;
  view: ServiceSettingsView;
  overrides: ServiceOverrides | undefined;
  save: (change: Parameters<typeof withService>[2]) => Promise<unknown>;
}) {
  const fixed = view.hosts_file_protected_containers;
  const added = overrides?.protected_containers ?? [];
  return (
    <section
      aria-label={`Service ${name}`}
      className="flex flex-col gap-3 border-t border-border pt-3 first:border-t-0 first:pt-0"
    >
      <h3 className="flex flex-wrap items-baseline gap-x-2 text-sm font-medium">
        {name}
        {name !== serviceKey && (
          <span className="font-mono text-xs font-normal text-muted-foreground">{serviceKey}</span>
        )}
      </h3>
      <div className="flex flex-col gap-1.5">
        <h4 className="text-xs font-medium">URLs to check</h4>
        <UrlEditor
          key={JSON.stringify(view.urls)}
          service={name}
          urls={view.urls}
          overridden={overrides?.urls !== undefined}
          onSave={(urls) => save({ urls })}
          onReset={() => save({ urls: undefined })}
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <h4 className="text-xs font-medium">Rules</h4>
        <ListEditor
          key={JSON.stringify(view.rules)}
          label={`Rules for ${name}`}
          items={view.rules}
          addLabel="Add a rule, such as: never restart the database during the day"
          emptyText="No rules."
          labels={RULE_LABELS}
          overridden={overrides?.rules !== undefined}
          onSave={(rules) => save({ rules })}
          onReset={() => save({ rules: undefined })}
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <h4 className="text-xs font-medium">Protected containers</h4>
        <p className="text-xs text-muted-foreground">
          Bleep never acts on these, even after an approval.
        </p>
        {fixed.length > 0 && (
          <ul
            aria-label={`Protected by the hosts file: ${name}`}
            className="flex flex-wrap gap-1.5"
          >
            {fixed.map((container) => (
              <li key={container}>
                <Badge title="Protected in the hosts file; only an edit there lifts it">
                  <Lock aria-hidden className="mr-1 size-3" />
                  <span className="font-mono">{container}</span>
                  <span className="sr-only"> (from the hosts file)</span>
                </Badge>
              </li>
            ))}
          </ul>
        )}
        <ListEditor
          key={JSON.stringify(added)}
          label={`Protected here: ${name}`}
          items={added}
          addLabel="Container name, such as shop-db-1"
          emptyText={fixed.length > 0 ? "None added here." : "None."}
          mono
          labels={CONTAINER_LABELS}
          onSave={(containers) =>
            save({ protected_containers: containers.length ? containers : undefined })
          }
        />
      </div>
    </section>
  );
}

/**
 * One host's settings as three panels: how Bleep reaches it (edit and remove for a dashboard
 * host), its rules, and each service's URLs, rules and protected containers.
 */
export function HostSettings({
  host,
  overrides,
  services,
  save,
  onHostChanged,
  onRemoved,
}: {
  host: HostSettingsView;
  overrides: HostOverrides | undefined;
  /** Services Bleep sees on the host now; those with settings but not seen are shown too. */
  services: KnownService[];
  save: (host: HostOverrides) => Promise<unknown>;
  /** Reads the settings again after the host was edited, or after a stale edit or removal. */
  onHostChanged?: () => Promise<void>;
  /** Called once the host is removed; without it the settings are read again. */
  onRemoved?: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const fromDashboard = host.connection.source === "dashboard" && onHostChanged !== undefined;
  const names = new Map(services.map((service) => [service.key, service.name]));
  const keys = [...new Set([...names.keys(), ...Object.keys(host.services)])].sort();
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
      <Panel title="Service settings">
        {keys.length === 0 ? (
          <p className="text-sm text-muted-foreground">Bleep sees no services on this host yet.</p>
        ) : (
          <div className="flex flex-col gap-4">
            {keys.map((key) => (
              <ServiceSettings
                key={key}
                serviceKey={key}
                name={names.get(key) ?? key}
                view={host.services[key] ?? NOTHING}
                overrides={overrides?.services?.[key]}
                save={(change) => save(withService(overrides, key, change))}
              />
            ))}
          </div>
        )}
      </Panel>
    </>
  );
}
