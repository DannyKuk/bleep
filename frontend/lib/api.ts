/** An error response from the backend: FastAPI's `{"detail": ...}` plus the status. */
export class ApiError extends Error {
  status: number;
  detail: string;
  retryAfter?: number; // seconds, from Retry-After (429)
  body: unknown; // the parsed JSON body, for fields beyond `detail` (a 409's `decided_via`)

  constructor(status: number, detail: string, retryAfter?: number, body: unknown = null) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.retryAfter = retryAfter;
    this.body = body;
  }
}

export type ApiInit = RequestInit & {
  /** Set to false where a 401 is an answer, not a lost session (the login form). */
  redirectOn401?: boolean;
};

function detailOf(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    return typeof detail === "string" ? detail : JSON.stringify(detail);
  }
  return fallback;
}

/** Same-origin call to the backend (Next rewrites /api/* to it). 401 sends the browser to login. */
export async function apiFetch<T>(path: string, init: ApiInit = {}): Promise<T> {
  const { redirectOn401 = true, headers, ...rest } = init;
  const response = await fetch(path, {
    ...rest,
    credentials: "same-origin",
    headers: {
      ...(rest.body ? { "content-type": "application/json" } : {}),
      ...headers,
    },
  });

  if (response.ok) {
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  const body = await response.json().catch(() => null);
  const retry = Number(response.headers.get("retry-after"));
  const error = new ApiError(
    response.status,
    detailOf(body, response.statusText || `HTTP ${response.status}`),
    Number.isFinite(retry) && retry > 0 ? retry : undefined,
    body,
  );

  if (response.status === 401 && redirectOn401 && window.location.pathname !== "/login") {
    const here = window.location.pathname + window.location.search;
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- a full load drops client state with the lost session
    window.location.assign(`/login?next=${encodeURIComponent(here)}`);
  }
  throw error;
}
