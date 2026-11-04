"use client";

import { Loader2 } from "lucide-react";
import { useEffect, useId, useState } from "react";

import { SaveError, useSaving } from "@/components/settings/save-error";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api";
import {
  addHost,
  changeHost,
  fetchHostKey,
  HOST_CHANGED,
  isHostStale,
  OFFER_LIFETIME_MS,
  removeHost,
  type HostKeyConfirmation,
  type HostKeyOffer,
} from "@/lib/hosts-api";
import type { HostSettingsView } from "@/lib/settings-types";

const FIELD_LABELS = {
  name: "Name",
  id: "ID",
  address: "Address",
  port: "Port",
  user: "User",
  docker_socket: "Docker socket",
  ssh_key: "SSH key",
  host_key: "Host key",
};

const slug = (name: string) =>
  name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);

interface Offered {
  offer: HostKeyOffer;
  expired: boolean;
}

/**
 * Trust on first use: fetches the host key the address offers and shows its fingerprint, and the
 * human ticks that it matches the one on the server. Gives the confirmation to the form only
 * while the offer is the one for this address and port, unexpired, and ticked.
 */
function HostKeyCheck({
  address,
  port,
  onConfirmation,
  disabled,
}: {
  address: string;
  port: number;
  onConfirmation: (confirmation: HostKeyConfirmation | null) => void;
  disabled: boolean;
}) {
  const id = useId();
  const [offered, setOffered] = useState<Offered | null>(null);
  const [ticked, setTicked] = useState(false);
  const [checking, setChecking] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const current =
    offered &&
    offered.offer.address === address.trim().toLowerCase() &&
    offered.offer.port === port &&
    !offered.expired
      ? offered.offer
      : null;
  const confirmed = current && ticked ? current : null;

  const offerId = offered?.offer.offer;
  useEffect(() => {
    if (!offerId) return;
    const timer = setTimeout(
      () => setOffered((now) => (now?.offer.offer === offerId ? { ...now, expired: true } : now)),
      OFFER_LIFETIME_MS,
    );
    return () => clearTimeout(timer);
  }, [offerId]);

  const fingerprint = confirmed?.fingerprint;
  useEffect(() => {
    onConfirmation(
      confirmed ? { offer: confirmed.offer, fingerprint: confirmed.fingerprint } : null,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reports changes of the confirmation only
  }, [confirmed?.offer, fingerprint]);

  async function check() {
    setChecking(true);
    setProblem(null);
    setTicked(false);
    try {
      setOffered({ offer: await fetchHostKey(address.trim(), port), expired: false });
    } catch (error) {
      setOffered(null);
      setProblem(
        error instanceof ApiError ? error.detail : "Could not reach Bleep to check the host key.",
      );
    } finally {
      setChecking(false);
    }
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={disabled || checking || address.trim() === ""}
          onClick={() => void check()}
        >
          {checking && <Loader2 aria-hidden className="animate-spin" />}
          Check the host key
        </Button>
        <span className="text-xs text-muted-foreground">The check saves nothing.</span>
      </div>
      {problem && (
        <p role="alert" className="text-xs text-health-down">
          {problem}
        </p>
      )}
      {offered && !current && (
        <p role="alert" className="text-xs text-health-down">
          {offered.expired
            ? "The host key offer expired. Check the host key again."
            : "The address or port changed since the check. Check the host key again."}
        </p>
      )}
      {current && (
        <div className="flex flex-col gap-1.5 rounded-lg border border-line p-2.5">
          <p className="text-xs">Bleep was offered this {current.key_type} host key:</p>
          <code aria-label="Offered host key fingerprint" className="font-mono text-sm break-all">
            {current.fingerprint}
          </code>
          <p className="text-xs text-muted-foreground">
            Compare it with the server&apos;s own: run{" "}
            <code className="font-mono">ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub</code> on
            it (use the key file of the type above). If it differs, stop: someone else may be
            answering at this address.
          </p>
          <label htmlFor={id} className="flex items-center gap-2 text-sm">
            <input
              id={id}
              type="checkbox"
              checked={ticked}
              disabled={disabled}
              onChange={(changed) => setTicked(changed.target.checked)}
            />
            This fingerprint matches the one on the server
          </label>
        </div>
      )}
    </div>
  );
}

function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: (id: string) => React.ReactNode;
  hint?: string;
}) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs font-medium">
        {label}
      </label>
      {children(id)}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

/**
 * Add a host (no `host`) or edit a dashboard host. The private key is typed into a textarea that
 * is emptied once the save goes through and is never filled from anything the API sends. The
 * page reads the settings again through `onChanged` after every save and after a stale one.
 */
export function HostForm({
  host,
  onChanged,
  onClose,
}: {
  host?: HostSettingsView;
  onChanged: () => Promise<void>;
  onClose?: () => void;
}) {
  const connection = host?.connection;
  const [name, setName] = useState(host?.name ?? "");
  const [hostId, setHostId] = useState(host?.id ?? "");
  const [idTouched, setIdTouched] = useState(false);
  const [address, setAddress] = useState(connection?.address ?? "");
  const [port, setPort] = useState(String(connection?.port ?? 22));
  const [user, setUser] = useState(connection?.user ?? "");
  const [socket, setSocket] = useState(connection?.docker_socket ?? "/var/run/docker.sock");
  const [key, setKey] = useState("");
  const [confirmation, setConfirmation] = useState<HostKeyConfirmation | null>(null);
  const { saving, busy, problems, run } = useSaving(FIELD_LABELS);

  const portNumber = Number(port);
  const portValid = Number.isInteger(portNumber) && portNumber >= 1 && portNumber <= 65535;
  const moved =
    connection !== undefined &&
    (address.trim().toLowerCase() !== connection.address ||
      portNumber !== connection.port ||
      user.trim() !== connection.user);
  const adding = host === undefined;
  const changed =
    moved ||
    key !== "" ||
    confirmation !== null ||
    name.trim() !== host?.name ||
    socket !== connection?.docker_socket;
  const ready = adding
    ? name.trim() !== "" &&
      hostId !== "" &&
      address.trim() !== "" &&
      user.trim() !== "" &&
      key.trim() !== "" &&
      confirmation !== null
    : changed && name.trim() !== "" && address.trim() !== "" && user.trim() !== "";

  async function save() {
    if (!portValid || (adding && !confirmation)) return;
    const fields = {
      name: name.trim(),
      address: address.trim(),
      port: portNumber,
      user: user.trim(),
      docker_socket: socket.trim(),
    };
    const saved = await run(async () => {
      try {
        if (adding && confirmation) {
          await addHost({ ...fields, id: hostId, ssh_key: key, host_key: confirmation });
        } else if (host && connection?.version) {
          await changeHost(
            host.id,
            {
              ...fields,
              ...(key === "" ? {} : { ssh_key: key }),
              ...(confirmation ? { host_key: confirmation } : {}),
            },
            connection.version,
          );
        }
      } catch (error) {
        if (!isHostStale(error)) throw error;
        await onChanged().catch(() => undefined);
        throw new Error(HOST_CHANGED);
      }
    });
    if (!saved) return;
    setKey("");
    setConfirmation(null);
    await onChanged();
    onClose?.();
  }

  return (
    <form
      aria-label={adding ? "Add a host" : `Edit ${host.name}`}
      className="flex flex-col gap-3"
      onSubmit={(submitted) => {
        submitted.preventDefault();
        void save();
      }}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name">
          {(id) => (
            <Input
              id={id}
              value={name}
              onChange={(changed) => {
                setName(changed.target.value);
                if (adding && !idTouched) setHostId(slug(changed.target.value));
              }}
            />
          )}
        </Field>
        {adding && (
          <Field label="ID" hint="Lowercase letters, digits and dashes; it cannot change later.">
            {(id) => (
              <Input
                id={id}
                className="font-mono"
                value={hostId}
                autoCapitalize="off"
                onChange={(changed) => {
                  setIdTouched(true);
                  setHostId(changed.target.value);
                }}
              />
            )}
          </Field>
        )}
        <Field label="Address">
          {(id) => (
            <Input
              id={id}
              className="font-mono"
              value={address}
              autoCapitalize="off"
              spellCheck={false}
              placeholder="box.lan or 10.0.0.5"
              onChange={(changed) => setAddress(changed.target.value)}
            />
          )}
        </Field>
        <Field label="Port">
          {(id) => (
            <Input
              id={id}
              className="font-mono"
              inputMode="numeric"
              value={port}
              aria-invalid={!portValid}
              onChange={(changed) => setPort(changed.target.value)}
            />
          )}
        </Field>
        <Field label="User">
          {(id) => (
            <Input
              id={id}
              className="font-mono"
              value={user}
              autoCapitalize="off"
              spellCheck={false}
              onChange={(changed) => setUser(changed.target.value)}
            />
          )}
        </Field>
        <Field label="Docker socket">
          {(id) => (
            <Input
              id={id}
              className="font-mono"
              value={socket}
              autoCapitalize="off"
              spellCheck={false}
              onChange={(changed) => setSocket(changed.target.value)}
            />
          )}
        </Field>
      </div>
      {moved && (
        <p role="note" className="text-xs text-health-degraded">
          A new address, port or user clears this host&apos;s stored SSH key and host key. Check the
          host key and enter the key again below, or the host waits for them.
        </p>
      )}
      <Field
        label={adding ? "SSH private key" : "New SSH private key"}
        hint={
          adding
            ? "Paste the private key Bleep logs in with. It is stored apart from the database and never shown again."
            : "Leave empty to keep the stored key. A new one is never shown again."
        }
      >
        {(id) => (
          <textarea
            id={id}
            value={key}
            rows={5}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            onChange={(changed) => setKey(changed.target.value)}
            className="min-h-24 w-full rounded-2xl border border-line bg-surface px-3 py-2 font-mono text-xs focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          />
        )}
      </Field>
      <HostKeyCheck
        address={address}
        port={portNumber}
        onConfirmation={setConfirmation}
        disabled={busy}
      />
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" size="sm" disabled={busy || !ready || !portValid}>
          {saving && <Loader2 aria-hidden className="animate-spin" />}
          {adding ? "Add host" : "Save"}
        </Button>
        {onClose && (
          <Button type="button" size="sm" variant="outline" disabled={busy} onClick={onClose}>
            Cancel
          </Button>
        )}
        {adding && !confirmation && (
          <span className="text-xs text-muted-foreground">
            Adding needs the key and a confirmed host key.
          </span>
        )}
      </div>
      <SaveError problems={problems} />
    </form>
  );
}

/** Remove with an inline second click; the warning says what happens to open incidents. */
export function RemoveHost({
  host,
  onChanged,
}: {
  host: HostSettingsView;
  onChanged: () => Promise<void>;
}) {
  const [sure, setSure] = useState(false);
  const { saving, busy, problems, run } = useSaving();
  const version = host.connection.version;

  async function remove() {
    if (!version) return;
    const removed = await run(async () => {
      try {
        await removeHost(host.id, version);
      } catch (error) {
        if (!isHostStale(error)) throw error;
        await onChanged().catch(() => undefined);
        throw new Error(HOST_CHANGED);
      }
    });
    if (removed) await onChanged();
    else setSure(false);
  }

  return (
    <div className="flex flex-col gap-1.5">
      {sure && (
        <p className="text-xs text-muted-foreground">
          Bleep stops watching {host.name} and deletes its keys. Its open incidents are closed with
          a notice.
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {sure ? (
          <>
            <Button type="button" size="sm" disabled={busy} onClick={() => void remove()}>
              {saving && <Loader2 aria-hidden className="animate-spin" />}
              Yes, remove this host
            </Button>
            <Button type="button" size="sm" variant="outline" onClick={() => setSure(false)}>
              Cancel
            </Button>
          </>
        ) : (
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => setSure(true)}
          >
            Remove
          </Button>
        )}
      </div>
      <SaveError problems={problems} />
    </div>
  );
}
