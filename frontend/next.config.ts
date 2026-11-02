import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Self-contained server in .next/standalone for the Docker image.
  output: "standalone",
  // The browser only ever talks to this origin, so the session cookie is same-origin. Next resolves
  // the destination at BUILD time (not when the server starts), so set BACKEND_URL for `next build`.
  async rewrites() {
    const backend = process.env.BACKEND_URL ?? "http://localhost:8000";
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};

export default nextConfig;
