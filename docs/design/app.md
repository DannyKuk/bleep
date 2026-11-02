# App: login, sessions and the read API

How the dashboard signs in and what it reads (`auth/`, `api/`).

- **One admin password, compared in constant time.** `hmac.compare_digest` against `BLEEP_ADMIN_PASSWORD`. Reason: a plain `==` leaks how many leading characters matched (#8).
- **Bleep refuses to start with an admin password under 12 characters,** with a message naming the variable. Reason: the login is the only thing between a LAN and a tool that can restart containers, and a short password is quick to guess (#8).
- **Sessions are random 32-byte tokens, stored as a sha256 hash.** The cookie holds the token; the `sessions` table holds only its hash. Reason: a leaked database file or backup cannot be replayed as a login (#8).
- **Seven-day sliding expiry.** `last_seen_at` and `expires_at` move forward on use, at most once a minute, and the cookie is renewed with them. Reason: a dashboard used daily should not log out mid-week, and one write per request is needless database churn (#8).
- **Cookie flags: `HttpOnly`, `SameSite=Lax`, `Path=/`, 7 days, `Secure` when `BLEEP_COOKIE_SECURE=true`.** Reason: HttpOnly keeps the token away from page scripts, Lax stops cross-site POSTs from carrying it, and Secure is opt-in because plain-http LAN use would otherwise never get a cookie (#8).
- **Login is rate limited: 5 failures per minute per client address.** A sixth attempt gets 429 with `Retry-After`, even with the right password, until the oldest failure leaves the window. Successful logins do not count. The state is in memory with an injectable clock. Reason: it stops guessing without locking the admin out for long, and a restart only forgives an attacker one minute (#8).
- **The client address is the direct peer, never `X-Forwarded-For`.** Reason: a header the client controls would let anyone reset their own limit; Bleep is LAN-only for now, so there is no trusted proxy to read it from (#8).
- **`require_session` guards every `/api` route except login,** through one router dependency, so a new route is protected by default. `/health` stays open and outside `/api`. A test walks the OpenAPI document and expects 401 on every other path. Reason: the safe default beats remembering to add a check (#8).
- **Response models are pydantic schemas (`schemas/api.py`).** Reason: `/openapi.json` gives the dashboard exact types. No model carries env values or labels, so a secret in a container's environment cannot reach a response (#8).

## Health rules

One pure function per level (`api/hosts.py`), table-tested for every state.

- **Container.** Running with health `healthy` or `none` is `ok`. Running and `starting`, or state `restarting`, is `degraded`. Running and `unhealthy`, or `exited`, `dead`, `created`, `paused`, is `down`. Any other state is `unknown`. Reason: degraded means "may fix itself", down means "needs someone"; a paused container serves nothing (#8).
- **Service.** The worst of its containers and its URLs, where a `failing` URL counts as `down`. With no data it is `unknown`. Reason: one dead dependency makes the service unusable (#8).
- **Host.** `unknown` when unreachable with no snapshot yet, `down` when unreachable, otherwise the worst of its services (`ok` with none). Reason: an unreachable host's old containers say nothing about now, but with no snapshot there is nothing to call down (#8).
- **URL status stays `unknown`** until the watcher records checks; `basics` is `null` until the host probe exists. Reason: the dashboard shape is fixed now so those land without an API change (#8).
