import { ApiError, apiFetch } from "@/lib/api";

/** A save refused because the settings changed since they were read; `message` is what the form
 * shows, in the language in force. */
export class SettingsChanged extends Error {
  constructor(message: string) {
    super(message);
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
