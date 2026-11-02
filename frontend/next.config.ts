import type { NextConfig } from "next";

// Next resolves both uses of this at BUILD time (not when the server starts), so set BACKEND_URL
// for `next build`: the rewrite destination goes into routes-manifest.json, and `env` inlines the
// value into the stream route handler.
const backend = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  // Self-contained server in .next/standalone for the Docker image.
  output: "standalone",
  env: { BACKEND_URL: backend },
  // The browser only ever talks to this origin, so the session cookie is same-origin. The live
  // stream (/api/stream) is a route handler instead, which wins over this rewrite: through the
  // rewrite Next gzips server-sent events and holds them back until the stream ends
  // (docs/decisions/sse-through-next.md).
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};

export default nextConfig;
