"use client";

import { Loader2 } from "lucide-react";
import { useEffect, useId, useState } from "react";

import { CodeText } from "@/components/code-text";
import { SaveError, useSaving } from "@/components/settings/save-error";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api";
import {
  addHost,
  changeHost,
  fetchHostKey,
  isHostStale,
  OFFER_LIFETIME_MS,
  removeHost,
  type HostKeyConfirmation,
  type HostKeyOffer,
} from "@/lib/hosts-api";
import { useT } from "@/lib/i18n";
import type { HostSettingsView } from "@/lib/settings-types";

const KEYGEN = 'ssh-keygen -t ed25519 -f bleep_key -C bleep -N ""';

function NewKeyHint() {
  const t = useT().settings.hostForm;
  return (
    <>
      <p>{t.keyPaste}</p>
      <p>{t.keyRecommend}</p>
      <code className="block w-fit max-w-full overflow-x-auto rounded-md bg-soft px-2 py-1 font-mono whitespace-nowrap text-ink">
        {KEYGEN}
      </code>
      <p>
        <CodeText text={t.keyThen} />
      </p>
    </>
  );
}

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
  const t = useT().settings.hostForm;

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
      setProblem(error instanceof ApiError ? error.detail : t.couldNotReach);
    } finally {
      setChecking(false);
    }
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          wrap
          type="button"
          size="sm"
          variant="outline"
          disabled={disabled || checking || address.trim() === ""}
          onClick={() => void check()}
        >
          {checking && <Loader2 aria-hidden className="animate-spin" />}
          {t.check}
        </Button>
        <span className="text-xs text-muted-foreground">{t.checkSavesNothing}</span>
      </div>
      {problem && (
        <p role="alert" className="text-xs text-health-down">
          {problem}
        </p>
      )}
      {offered && !current && (
        <p role="alert" className="text-xs text-health-down">
          {offered.expired ? t.offerExpired : t.offerMoved}
        </p>
      )}
      {current && (
        <div className="flex flex-col gap-1.5 rounded-lg border border-line p-2.5">
          <p className="text-xs">{t.offered(current.key_type)}</p>
          <code aria-label={t.fingerprintLabel} className="font-mono text-sm break-all">
            {current.fingerprint}
          </code>
          <p className="text-xs text-muted-foreground">
            <CodeText text={t.compare} />
          </p>
          <label htmlFor={id} className="flex items-center gap-2 text-sm">
            <input
              id={id}
              type="checkbox"
              checked={ticked}
              disabled={disabled}
              onChange={(changed) => setTicked(changed.target.checked)}
            />
            {t.matches}
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
  hint?: React.ReactNode;
}) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs font-medium">
        {label}
      </label>
      {children(id)}
      {hint && <div className="flex flex-col gap-1 text-xs text-muted-foreground">{hint}</div>}
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
  const { hostForm: t, save: saveWord, cancel } = useT().settings;
  const { saving, busy, problems, run } = useSaving(t.labels);

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
  const fieldsFilled = name.trim() !== "" && address.trim() !== "" && user.trim() !== "";
  const needsKeys = adding || moved;
  const ready = needsKeys
    ? fieldsFilled && (!adding || hostId !== "") && key.trim() !== "" && confirmation !== null
    : changed && fieldsFilled;

  async function save() {
    if (!portValid || !ready) return;
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
        throw new Error(t.changed);
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
      aria-label={adding ? t.addForm : t.editForm(host.name)}
      className="flex flex-col gap-3"
      onSubmit={(submitted) => {
        submitted.preventDefault();
        void save();
      }}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={t.labels.name}>
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
          <Field label={t.labels.id} hint={t.idHint}>
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
        <Field label={t.labels.address}>
          {(id) => (
            <Input
              id={id}
              className="font-mono"
              value={address}
              autoCapitalize="off"
              spellCheck={false}
              placeholder={t.addressPlaceholder}
              onChange={(changed) => setAddress(changed.target.value)}
            />
          )}
        </Field>
        <Field label={t.labels.port}>
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
        <Field label={t.labels.user}>
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
        <Field label={t.labels.docker_socket}>
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
          {t.moved}
        </p>
      )}
      <Field label={adding ? t.newKey : t.newKeyEdit} hint={adding ? <NewKeyHint /> : t.keepStored}>
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
        <Button wrap type="submit" size="sm" disabled={busy || !ready || !portValid}>
          {saving && <Loader2 aria-hidden className="animate-spin" />}
          {adding ? t.addHost : saveWord}
        </Button>
        {onClose && (
          <Button wrap type="button" size="sm" variant="outline" disabled={busy} onClick={onClose}>
            {cancel}
          </Button>
        )}
        {needsKeys && !ready && (
          <span className="text-xs text-muted-foreground">{t.needsKeys}</span>
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
  onRemoved = onChanged,
}: {
  host: HostSettingsView;
  /** Reads the settings again, after a removal or a stale one. */
  onChanged: () => Promise<void>;
  /** Called once the host is removed, instead of `onChanged`. */
  onRemoved?: () => Promise<void>;
}) {
  const [sure, setSure] = useState(false);
  const { hostForm: t, cancel } = useT().settings;
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
        throw new Error(t.changed);
      }
    });
    if (removed) await onRemoved();
    else setSure(false);
  }

  return (
    <div className="flex flex-col gap-1.5">
      {sure && <p className="text-xs text-muted-foreground">{t.removeWarning(host.name)}</p>}
      <div className="flex flex-wrap items-center gap-2">
        {sure ? (
          <>
            <Button wrap type="button" size="sm" disabled={busy} onClick={() => void remove()}>
              {saving && <Loader2 aria-hidden className="animate-spin" />}
              {t.removeYes}
            </Button>
            <Button wrap type="button" size="sm" variant="outline" onClick={() => setSure(false)}>
              {cancel}
            </Button>
          </>
        ) : (
          <Button
            wrap
            type="button"
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => setSure(true)}
          >
            {t.remove}
          </Button>
        )}
      </div>
      <SaveError problems={problems} />
    </div>
  );
}
