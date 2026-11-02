# App

The dashboard and the API it reads. One bullet per choice, with the reason and the issue number.

## Login, sessions and the read API

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

### Health rules

One pure function per level (`api/hosts.py`), table-tested for every state.

- **Container.** Running with health `healthy` or `none` is `ok`. Running and `starting`, or state `restarting`, is `degraded`. Running and `unhealthy`, or `exited`, `dead`, `created`, `paused`, is `down`. Any other state is `unknown`. Reason: degraded means "may fix itself", down means "needs someone"; a paused container serves nothing (#8).
- **Service.** The worst of its containers and its URLs, where a `failing` URL counts as `down`. With no data it is `unknown`. Reason: one dead dependency makes the service unusable (#8).
- **Host.** `unknown` when unreachable with no snapshot yet, `down` when unreachable, otherwise the worst of its services (`ok` with none). Reason: an unreachable host's old containers say nothing about now, but with no snapshot there is nothing to call down (#8).
- **URL status stays `unknown`** until the watcher records checks; `basics` is `null` until the host probe exists. Reason: the dashboard shape is fixed now so those land without an API change (#8).

## Dashboard

How the Next.js dashboard (`frontend/`) is built.

- **Rewrite proxy, not CORS.** `next.config.ts` rewrites `/api/:path*` to the backend, so the browser only talks to the dashboard's origin and the `bleep_session` cookie (HttpOnly, SameSite=Lax) is same-origin. Reason: no CORS config, no cross-site cookie, and the backend port is never exposed to the browser (#9).
- **`BACKEND_URL` is a build argument.** Next bakes rewrite destinations into `routes-manifest.json` at `next build`; setting the variable only when the server starts has no effect (checked by running the standalone server with a different value). The Dockerfile takes `ARG BACKEND_URL` with the default `http://backend:8000`, the Compose service name. Compose (Task 10) must therefore pass `build.args.BACKEND_URL` for the frontend service. Reason: a silent runtime variable that does nothing is worse than a documented build argument (#9).
- **A 401 sends the browser to `/login?next=<path>`.** `apiFetch` does this once for every call, except on the login page itself, where a 401 means "wrong password". The login form only follows a `next` that the URL parser resolves to the same origin (a prefix check misses `/\t/evil.com`, because parsers drop tabs and newlines). Reason: one place handles an expired session, and `next` cannot be used as an open redirect (#9).
- **Polling every 10 s until live events.** `usePoll` fetches `/api/hosts` and `/api/services` on an interval and keeps the last good data when a refresh fails, with a small "refresh failed" line. It pauses while the tab is hidden (so an idle tab does not slide the session) and refetches when it becomes visible; each tick aborts the previous one and carries a sequence number, so a slow old response never overwrites newer data; hosts and services come from one `Promise.all` and are committed together. Reason: the simplest thing that works against the M1 API; server-sent events replace it in Task 23 (#9).
- **Health colours: green, amber, red, grey, never alone.** `--health-ok`, `--health-degraded`, `--health-down` and `--health-unknown` are CSS variables with a light and a dark value, used as text on a 12% tint of themselves. Every health also has its own icon (check, triangle, cross, question mark) and a word, so colour-blind readers and greyscale prints lose nothing. Reason: an ops screen is read under stress and at a glance; colour is a quick cue, not the only one (#9).
- **Quiet when all is well.** Healthy rows use small tinted badges and no borders or alarms; only down and degraded things use stronger colour, plus the incident link. Reason: the eye should land on what is wrong (#9).
- **Monospace for container names, ids and URLs.** Reason: they are copied into terminals and compared character by character (#9).
- **Theme: system, light or dark, kept across reloads.** The pick lives in `localStorage` (`bleep:theme`); an inline script in `<head>` sets the `dark` class on `<html>` before the first paint, so there is no flash. "System" follows the OS setting live. Reason: same approach as the owner's other projects; reading storage only after hydration would flash the wrong theme on every load (#9).
- **Unit tests mock `fetch`, nothing else.** Vitest with Testing Library and jsdom; the poll and login tests stub the global `fetch`, so no test needs a backend or network. Reason: the dashboard is built against the API contract in parallel with the backend (#9).
- **Types are written by hand for now.** `lib/api-types.ts` follows the M1 contract; `npm run gen:api` replaces it with types generated from the backend's `/openapi.json` once that is running. The names `HostView`, `ServiceView`, `ContainerView`, `UrlStatus` and `Health` stay (#9).
