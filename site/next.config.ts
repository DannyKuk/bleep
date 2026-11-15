import path from "node:path";

import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Plain HTML, CSS and JS in out/, served by any static web server.
  output: "export",
  images: { unoptimized: true },
  // The page draws Bleep from the dashboard's own component (tsconfig maps `@/` to ../frontend),
  // so the build has to see the whole repository.
  turbopack: { root: path.join(import.meta.dirname, "..") },
};

export default nextConfig;
