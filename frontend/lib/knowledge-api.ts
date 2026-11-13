import { apiFetch } from "@/lib/api";

/** How the last read a person asked for ended: `failed`, or `read` of the `linked` repos. */
export interface RepoDocsResult {
  failed: boolean;
  read: number;
  linked: number;
  embedded: number;
}

/** `GET /api/knowledge/repos`: what Bleep holds of the linked repos' docs. */
export interface RepoDocsState {
  /** When the repos were last read; null before the first read. */
  last_read_at: string | null;
  repos: { repo: string; chunks: number }[];
  /** A read is running; it can take over a minute, so the page asks again until it is false. */
  reading: boolean;
  /** How the last read a person asked for ended; null until one has. */
  result: RepoDocsResult | null;
}

export const fetchRepoDocs = (signal?: AbortSignal) =>
  apiFetch<RepoDocsState>("/api/knowledge/repos", { signal });

/** `POST /api/knowledge/repos/refresh` starts the read and answers at once (202), reading. */
export const startRepoRefresh = () =>
  apiFetch<RepoDocsState>("/api/knowledge/repos/refresh", { method: "POST" });
