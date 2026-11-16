const BACKEND = process.env.BACKEND_URL ?? "http://localhost:8000";

/**
 * Whether the backend runs with its login off (BLEEP_LOGIN_ENABLED=false, #611): then `/api/auth/me`
 * answers a request that carries no session. Asked from the server, so the login page can send the
 * browser home before it draws a form. A backend that is down or refuses counts as on.
 */
export async function loginIsOff(backend = BACKEND): Promise<boolean> {
  try {
    const response = await fetch(`${backend}/api/auth/me`, { cache: "no-store" });
    return response.ok;
  } catch {
    return false;
  }
}
