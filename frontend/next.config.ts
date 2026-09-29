import type { NextConfig } from "next";

// The dashboard talks to the FastAPI backend through a same-origin rewrite (no CORS in the browser).
const API = process.env.DCE_API_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  devIndicators: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/api/:path*` }];
  },
};

export default nextConfig;
