import { ApiError, apiFetch } from "@/lib/api";

/**
 * One secret or plugin address the dashboard may set, as `GET /api/secrets` lists it. No field
 * holds a secret: `value` is an address's origin (set on the dashboard only) or a masked id.
 */
export interface SecretView {
  name: string;
  kind: "secret" | "address" | "id";
  set: boolean;
  source: "env" | "dashboard" | null;
  /** The environment decides it: the dashboard can neither set nor clear it. */
  env_owned: boolean;
  /** An address: the token sent to it, cleared when the address changes. */
  carries: string | null;
  value: string | null;
  /** An address set on the dashboard: sent back with the token typed for it. */
  version: string | null;
  /** An address set on the dashboard that the API would refuse now (the file was edited by hand):
   * set, shown with no value, and its plugin cannot be switched on until it is entered again. */
  invalid: boolean;
  /** A model server's address, which may be private or plain http to one (#372). */
  private?: boolean;
}

export interface SecretActions {
  /** `addressVersion` is the `version` of the address a token was typed for. */
  set: (name: string, value: string, addressVersion?: string) => Promise<unknown>;
  clear: (name: string) => Promise<unknown>;
}

/** `PUT /api/secrets/{name}`; answers the whole list. The value is never kept by this module. */
export function putSecret(
  name: string,
  value: string,
  addressVersion?: string,
): Promise<SecretView[]> {
  return apiFetch<SecretView[]>(`/api/secrets/${encodeURIComponent(name)}`, {
    method: "PUT",
    body: JSON.stringify(
      addressVersion === undefined ? { value } : { value, address_version: addressVersion },
    ),
  });
}

/** `DELETE /api/secrets/{name}`; answers the whole list. */
export function deleteSecret(name: string): Promise<SecretView[]> {
  return apiFetch<SecretView[]>(`/api/secrets/${encodeURIComponent(name)}`, { method: "DELETE" });
}

/** A token refused because its address changed since the page read it (409). */
export const isAddressChanged = (error: unknown) =>
  error instanceof ApiError && error.status === 409;
