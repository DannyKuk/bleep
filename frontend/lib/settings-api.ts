import { ApiError, apiFetch } from "@/lib/api";

export const NOT_SAVED = "Not saved: the settings changed elsewhere since this page read them.";
export const SETTINGS_CHANGED = `${NOT_SAVED} They have been read again; check them and make your change again if it is still needed.`;

/** What to say when the settings could not be read again after a 409: the page is still stale. */
export const rereadFailed = (error: unknown) =>
  `${NOT_SAVED} Reading them again failed (${error instanceof Error ? error.message : String(error)}); reload the page before you change anything else.`;

/** A save refused because the settings changed since they were read. */
export class SettingsChanged extends Error {
  constructor() {
    super(NOT_SAVED);
    this.name = "SettingsChanged";
  }
}

/**
 * `PUT /api/settings` built from the settings at `version` (the `version` of the `GET` the body
 * was made from). The API answers 409 when they have changed since, and applies nothing.
 */
export function putSettings<T>(update: object, version: string): Promise<T> {
  return apiFetch<T>("/api/settings", {
    method: "PUT",
    body: JSON.stringify(update),
    headers: { "if-match": `"${version}"` },
  });
}

/** `PUT /api/plugins/{name}` built from the plugin at `version` (its own, from `GET /api/plugins`). */
export function putPlugin<T>(name: string, update: object, version: string): Promise<T> {
  return apiFetch<T>(`/api/plugins/${encodeURIComponent(name)}`, {
    method: "PUT",
    body: JSON.stringify(update),
    headers: { "if-match": `"${version}"` },
  });
}

export const isStale = (error: unknown) => error instanceof ApiError && error.status === 409;
