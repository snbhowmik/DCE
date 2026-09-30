import type { NextConfig } from "next";

// The dashboard talks to the FastAPI backend through a same-origin rewrite (no CORS in the browser).
const API = process.env.DCE_API_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  devIndicators: false,
  // what-ifs on a world with no cached forecast can take ~30–60 s the first time
  experimental: { proxyTimeout: 180_000 },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/api/:path*` }];
  },
};

export default nextConfig;
