/** How long the backend has to answer before the login counts as on. */
export const ANSWER_MS = 2000;
const BACKEND = process.env.BACKEND_URL ?? "http://localhost:8000";

/**
 * Whether the backend runs with its login off (BLEEP_LOGIN_ENABLED=false, #611): then `/api/auth/me`
 * answers a request that carries no session. Asked from the server, so the login page can send the
 * browser home before it draws a form. A backend that is down, refuses or does not answer within
 * `ANSWER_MS` counts as on, so the page that asks never hangs on it.
 */
export async function loginIsOff(backend = BACKEND): Promise<boolean> {
  try {
    const response = await fetch(`${backend}/api/auth/me`, {
      cache: "no-store",
      signal: AbortSignal.timeout(ANSWER_MS),
    });
    return response.ok;
  } catch {
    return false;
  }
}
