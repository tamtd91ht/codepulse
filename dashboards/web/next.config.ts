import type { NextConfig } from "next";

// The dashboard proxies /api to the FastAPI service, so the session cookie and the GitLab
// OAuth callback live on the dashboard's own origin (set APP_BASE_URL to the dashboard URL).
const apiUrl = process.env.API_URL ?? "http://localhost:8000";

const config: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/api/:path*` }];
  },
};

export default config;
