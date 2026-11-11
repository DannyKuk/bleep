import { ApiError, apiFetch } from "@/lib/api";

/** `POST /api/settings/hosts/host-key`: the host key a server offers, to confirm by fingerprint. */
export interface HostKeyOffer {
  offer: string;
  address: string;
  port: number;
  fingerprint: string;
  key_type: string;
}

/** How long an offer can be confirmed after it was fetched (the backend's `OFFER_TTL`). */
export const OFFER_LIFETIME_MS = 10 * 60 * 1000;

export interface HostKeyConfirmation {
  offer: string;
  fingerprint: string;
}

export interface HostConnectionFields {
  name: string;
  address: string;
  port: number;
  user: string;
  docker_socket: string;
}

export interface NewHost extends HostConnectionFields {
  id: string;
  ssh_key: string;
  host_key: HostKeyConfirmation;
}

/** `ssh_key` or `host_key` left out keeps (or, after a new address, leaves empty) that one. */
export interface HostEdit extends HostConnectionFields {
  ssh_key?: string;
  host_key?: HostKeyConfirmation;
}

const ifMatch = (version: string) => ({ "if-match": `"${version}"` });

export const fetchHostKey = (address: string, port: number) =>
  apiFetch<HostKeyOffer>("/api/settings/hosts/host-key", {
    method: "POST",
    body: JSON.stringify({ address, port }),
  });

export const addHost = (host: NewHost) =>
  apiFetch<unknown>("/api/settings/hosts", { method: "POST", body: JSON.stringify(host) });

export const changeHost = (id: string, edit: HostEdit, version: string) =>
  apiFetch<unknown>(`/api/settings/hosts/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify(edit),
    headers: ifMatch(version),
  });

export const removeHost = (id: string, version: string) =>
  apiFetch<void>(`/api/settings/hosts/${encodeURIComponent(id)}`, {
    method: "DELETE",
    headers: ifMatch(version),
  });

/** A change or removal refused because the host changed since it was read: a 409 that carries the
 * current `version`, unlike "that id is taken". */
export const isHostStale = (error: unknown) =>
  error instanceof ApiError &&
  error.status === 409 &&
  typeof (error.body as { version?: unknown } | null)?.version === "string";
