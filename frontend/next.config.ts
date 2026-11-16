import type { NextConfig } from "next";

// Next resolves both uses of this at BUILD time (not when the server starts), so set BACKEND_URL
// for `next build`: the rewrite destination goes into routes-manifest.json, and `env` inlines the
// value into the stream route handler.
const backend = process.env.BACKEND_URL ?? "http://localhost:8000";

// With the login off (#611) a framed dashboard's "Yes" click would be a same-origin request, so no
// page may be framed. Only frame-ancestors: a broader CSP would break the app's inline styles.
export const FRAMING_HEADERS = [
  { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
  { key: "X-Frame-Options", value: "DENY" },
];

const nextConfig: NextConfig = {
  // Self-contained server in .next/standalone for the Docker image.
  output: "standalone",
  env: { BACKEND_URL: backend },
  // The browser only ever talks to this origin, so the session cookie is same-origin. The live
  // stream (/api/stream) is a route handler instead, which wins over this rewrite: through the
  // rewrite Next gzips server-sent events and holds them back until the stream ends
  // (docs/decisions/sse-through-next.md).
  async headers() {
    return [{ source: "/:path*", headers: FRAMING_HEADERS }];
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};

export default nextConfig;
