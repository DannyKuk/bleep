import { apiFetch } from "@/lib/api";

/** `GET /api/knowledge/repos`: what Bleep holds of the linked repos' docs. */
export interface RepoDocsState {
  /** When the repos were last read; null before the first read. */
  last_read_at: string | null;
  repos: { repo: string; chunks: number }[];
}

/** `POST /api/knowledge/repos/refresh`: the state after, with what the refresh did. */
export interface RepoDocsRefreshed extends RepoDocsState {
  read: number;
  linked: number;
  embedded: number;
}

export const refreshRepoDocs = () =>
  apiFetch<RepoDocsRefreshed>("/api/knowledge/repos/refresh", { method: "POST" });
